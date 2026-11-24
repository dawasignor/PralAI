"""Resolving a place name to coordinates, and a rough date to a real event.

A user types "Boston" and means a bounding box; they type "last November" and
mean a flood that has a start date, an end date and usually a GLIDE number.
Neither is something to guess at, and both are cheap to look up.

Two providers, both keyless:

  * **Nominatim (OpenStreetMap)**, through geopy, for the gazetteer. It returns
    a bounding box directly, which is what every workflow in this repo wants.
    It is a community gazetteer, not an official one, so what comes back is
    marked as such -- a bounding box is a fine thing to take from OSM, and a
    claim about a place is not.
  * **GDACS** for the event. The Global Disaster Alert and Coordination System
    is run by the European Commission's Joint Research Centre with UN OCHA, and
    it is the one register that gives a window rather than only an onset: an
    event carries `fromdate` and `todate`, which is exactly what a time frame
    needs. The JRC is already a cited provider in this repo, for Global Surface
    Water.

    ReliefWeb was the first choice and is not usable without paperwork: its v1
    API now returns 410, and v2 rejects any appname that has not been approved
    through a request form. Wikipedia would have been easier than either, and
    is inadmissible the moment the user turns on verified-sources-only.

Both calls fail soft. A resolver that cannot reach the network should hand back
"I could not confirm this, here is what you told me" -- not an exception that
takes the turn down with it.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from typing import Any

# Nominatim's usage policy requires an identifying agent string and at most one
# request a second. Both are honoured below; geopy enforces neither for us.
USER_AGENT = "pralai-flood-research/0.1 (research; contact via repo)"
TIMEOUT = 12
RETRY_PAUSE = 1.5

OSM_SOURCE = "OpenStreetMap Nominatim (community gazetteer)"
GDACS_SOURCE = "GDACS (JRC / UN OCHA)"

# The disaster register is switched OFF for the thesis work.
#
# It was there to anchor a rough time frame to a recorded event. The thesis
# supplies its own event window, so a second opinion from a register is not
# corroboration -- it is a different window arriving after the right one, and
# it won twice: it overrode a user-stated 2025-11-24..12-01 with Ditwah's own
# 11-27..11-29, and it stopped a turn to ask which of two Sri Lankan entries
# was meant when neither was needed.
#
# Switched rather than deleted. `find_event` still exists and still works;
# flipping this back to True restores every caller with no other change, which
# is what keeps this reversible when a run DOES need a register lookup.
GDACS_ENABLED = False
OVERPASS_SOURCE = "OpenStreetMap Overpass API (community survey data)"

# Mirrors, tried in order. The main instance load-sheds with a 504 on the
# heavier queries -- an unqualified building count over a city is enough to do
# it -- and that is a property of which server answered, not of the query, so a
# refusal moves on rather than asking the busy server again.
#
# EVERY ENTRY HERE MUST CARRY THE WHOLE PLANET. A regional instance does not
# fail on an AOI it has no data for: it answers 200 with a count of zero, which
# arrives as the finding "nothing mapped here" with nothing to mark it false.
# That is strictly worse than a timeout, because a timeout is visible.
#
# overpass.osm.ch was in this list and is the worked example. Measured on the
# same bbox and the same query:
#
#     overpass-api.de   Derna 14657   Zurich 21780     <- global
#     overpass.osm.ch   Derna     0   Zurich 21780     <- Switzerland only
#
# It looked like a healthy mirror in every way except the number. Do not add an
# endpoint here without running that two-AOI check against it first: one AOI
# inside its region and one far outside, and the far one must not come back 0.
# There is one entry, and adding a second made things worse both times it was
# tried. overpass.kumi.systems is global and legitimate and does not answer
# from here at all -- every fallback to it spent the pause plus the full read
# timeout and returned nothing, which took the *budget* from categories that
# would have succeeded: six categories went to one. A fallback that never
# answers is not redundancy, it is a tax on every failure.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
)
OVERPASS_URL = OVERPASS_URLS[0]

# Passes over the endpoint list. With one endpoint this is "try twice, with a
# pause", which is what the measurements supported: 429 and 504 here are load,
# and load passes.
ATTEMPTS = 2

# Overpass counts every matching element in the box, and a box the size of a
# province is a query that times out rather than one that answers slowly.
# Refusing it here costs nothing; letting it through costs the whole turn,
# because a workflow owns its own timeout and nothing above it interrupts one.
MAX_AOI_KM2 = 5_000

# Spacing between Overpass requests, and the longer wait before retrying one
# the server refused. The public instance is a shared resource with a published
# etiquette; these are the cost of using it politely rather than tuning knobs.
PACE = 2.0
BUSY_PAUSE = 12.0

# What the exposure count is *for*. A flood analysis asks what sits in the
# water, so the categories are the ones a responder would name: where people
# sleep, where the sick are treated, where the shops and the jobs are.
# `buildings` is the denominator the rest are read against.
#
# Each entry is (selector, element types). Buildings are mapped as areas, so
# querying nodes for them scans a vastly larger table for nothing. Amenities
# are often a single node -- a clinic with no footprint drawn -- so those do
# need it, and that asymmetry is most of what keeps the query inside its
# timeout.
#
# These are counts of *mapped* features, which is not the same as counts of
# features. OSM coverage is excellent in some cities and thin in others, and a
# low count can mean either few buildings or few surveyors. Never report one of
# these as a population or an asset total.
FEATURES: dict[str, tuple[str, tuple[str, ...]]] = {
    "buildings": ('["building"]', ("way", "relation")),
    "houses": ('["building"~"^(house|detached|semidetached_house|bungalow|terrace)$"]',
               ("way", "relation")),
    "apartments": ('["building"~"^(apartments|residential|dormitory)$"]',
                   ("way", "relation")),
    "commercial": ('["building"~"^(commercial|retail|office|industrial|warehouse)$"]',
                   ("way", "relation")),
    "hospitals": ('["amenity"~"^(hospital|clinic|doctors)$"]',
                  ("node", "way", "relation")),
    "schools": ('["amenity"~"^(school|college|university)$"]',
                ("node", "way", "relation")),
}

# GDACS hazard codes. Named here rather than passed as bare letters, so a
# caller narrowing the search does not have to guess the vocabulary.
HAZARDS = {"FL": "flood", "TC": "tropical cyclone", "EQ": "earthquake",
           "DR": "drought", "WF": "wildfire", "VO": "volcano"}


def geocode_place(query: str, limit: int = 5) -> dict[str, Any]:
    """Candidate places for a name, each with a bounding box.

    Returns `{"ok": bool, "candidates": [...], "source": str, "problem": str}`.
    Several candidates is the normal case and the interesting one -- "Boston"
    is a real city in Lincolnshire as well as Massachusetts, and choosing
    silently between them is how an analysis ends up over the wrong continent.
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "candidates": [], "source": OSM_SOURCE,
                "problem": "no place name given"}

    try:
        from geopy.geocoders import Nominatim
    except ImportError:
        return {"ok": False, "candidates": [], "source": OSM_SOURCE,
                "problem": "geopy is not installed (pip install geopy)"}

    try:
        locator = Nominatim(user_agent=USER_AGENT, timeout=TIMEOUT)
        # language="en": without it Nominatim answers in the local script, and
        # the AOI line a user is asked to confirm comes back in Devanagari.
        matches = locator.geocode(query, exactly_one=False, limit=limit,
                                  addressdetails=True, language="en") or []
    except Exception as exc:                                   # noqa: BLE001
        # Nominatim rate-limits, times out and occasionally 502s. None of that
        # is worth a traceback in a chat window.
        return {"ok": False, "candidates": [], "source": OSM_SOURCE,
                "problem": f"{type(exc).__name__}: {exc}"}

    candidates = []
    for match in matches:
        raw = match.raw or {}
        address = raw.get("address") or {}
        box = raw.get("boundingbox")
        bbox = None
        if box and len(box) == 4:
            # Nominatim orders it (south, north, west, east); every workflow
            # here takes (west, south, east, north).
            south, north, west, east = (float(v) for v in box)
            bbox = (west, south, east, north)
        candidates.append({
            "display": raw.get("display_name") or str(match),
            "bbox": bbox,
            "lat": float(raw["lat"]) if raw.get("lat") else None,
            "lon": float(raw["lon"]) if raw.get("lon") else None,
            "country": address.get("country", ""),
            "admin": address.get("state") or address.get("region") or "",
            "kind": raw.get("type", ""),
            # Nominatim ranks by importance; carried through so the caller can
            # tell "one obvious answer" from "five equally plausible ones".
            "importance": float(raw.get("importance") or 0.0),
        })
    return {"ok": bool(candidates), "candidates": candidates,
            "source": OSM_SOURCE,
            "problem": "" if candidates else f"no place matched {query!r}"}


def _gdacs(params: list[tuple[str, str]]) -> Any:
    url = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?"
           + urllib.parse.urlencode(params))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    # One retry. GDACS drops connections without a response under load -- a
    # RemoteDisconnected rather than an HTTP status -- and the second attempt
    # usually lands. Two attempts, not more: a register that is genuinely down
    # should reach the caller as "down" quickly, so the answer can say so.
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:                               # noqa: BLE001
            last = exc
            if attempt == 1:
                time.sleep(RETRY_PAUSE)
    raise last                                                 # type: ignore[misc]


def find_event(country: str, start: str, end: str,
               kinds: str = "") -> dict[str, Any]:
    """Recorded disasters in a country over a date range, newest first.

    GDACS indexes by country, not by city, so `country` is what the gazetteer
    returned for the AOI rather than what the user typed. Widen `start` and
    `end` before calling: an event that began three days before the window the
    user described is almost certainly the one they meant.

    `kinds` is a comma-separated list of HAZARDS codes. Empty means every
    hazard, which is the honest default -- a question about a flood week can
    legitimately be answered by the cyclone that caused it.
    """
    if not GDACS_ENABLED:
        # Reported as disabled, never as "no events found". The two lead to
        # different behaviour in `core/resolve.py` and must not look alike:
        # an empty register invites a question, a disabled one does not.
        return {"ok": False, "events": [], "disabled": True,
                "source": GDACS_SOURCE,
                "problem": "the disaster register is switched off "
                           "(places.GDACS_ENABLED is False)"}
    return gdacs_events(country, start, end, kinds)


def gdacs_events(country: str, start: str, end: str,
                 kinds: str = "") -> dict[str, Any]:
    """The GDACS query itself, without the GDACS_ENABLED switch.

    The switch stops the register from SETTLING a window. Listing what it holds
    for an event, as links beside an answer (core/event_records.py), decides
    nothing and is not what the switch is for.
    """
    if not (country and start and end):
        return {"ok": False, "events": [], "source": GDACS_SOURCE,
                "problem": "country, start and end are all required"}

    params = [("fromDate", start), ("toDate", end), ("country", country)]
    if kinds:
        params.append(("eventlist", kinds))
    try:
        payload = _gdacs(params)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "events": [], "source": GDACS_SOURCE,
                "problem": f"{type(exc).__name__}: {exc}"}

    events = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        kind = props.get("eventtype", "")
        events.append({
            "name": props.get("name") or props.get("description", ""),
            "kind": HAZARDS.get(kind, kind),
            "start": (props.get("fromdate") or "")[:10],
            "end": (props.get("todate") or "")[:10],
            # GDACS carries a GLIDE number only sometimes, and an empty string
            # is not a missing field to paper over -- it means this event was
            # never assigned one.
            "glide": props.get("glide", ""),
            "severity": props.get("alertlevel", ""),
            "country": props.get("country", ""),
            "url": (props.get("url") or {}).get("report", ""),
        })
    events.sort(key=lambda e: e["start"], reverse=True)
    return {"ok": bool(events), "events": events, "source": GDACS_SOURCE,
            "problem": "" if events else
                       f"no {GDACS_SOURCE} record in {country} for {start}..{end}"}


def country_at(lon: float, lat: float) -> str:
    """The country a point lies in, in English, or "" when it cannot be told.

    For an AOI the user gave as coordinates: it was never geocoded, so the
    record holds no country, and every disaster register indexes by country.
    One Nominatim reverse lookup at zoom 3 (country level).
    """
    try:
        from geopy.geocoders import Nominatim
        found = Nominatim(user_agent=USER_AGENT, timeout=TIMEOUT).reverse(
            (lat, lon), language="en", zoom=3, addressdetails=True)
    except Exception:                                          # noqa: BLE001
        return ""
    return ((found.raw or {}).get("address") or {}).get("country", "") if found else ""


def country_code_at(lon: float, lat: float) -> str:
    """The ISO 3166-1 alpha-2 code of the country a point lies in, upper
    case, or "" when it cannot be told. One Nominatim reverse lookup."""
    try:
        from geopy.geocoders import Nominatim
        found = Nominatim(user_agent=USER_AGENT, timeout=TIMEOUT).reverse(
            (lat, lon), language="en", zoom=3, addressdetails=True)
    except Exception:                                          # noqa: BLE001
        return ""
    return (((found.raw or {}).get("address") or {}).get("country_code", "") or "").upper()         if found else ""


def bbox_km2(bbox: tuple[float, float, float, float] | list[float]) -> float:
    """Rough ground area of a WGS84 bounding box, in square kilometres.

    A cosine correction on the mid-latitude, not a projection. It is used to
    decide whether a query is too big to attempt, and being a few percent out
    on that decision changes nothing.
    """
    west, south, east, north = (float(v) for v in bbox)
    mid = math.radians((south + north) / 2)
    return abs(east - west) * 111.32 * math.cos(mid) * abs(north - south) * 110.57


def osm_features(bbox: tuple[float, float, float, float] | list[float],
                 timeout: int = 90, deadline_s: float = 300.0) -> dict[str, Any]:
    """Counts of mapped buildings and facilities inside a bounding box.

    The gazetteer says where the AOI is; this says what is in it. One Overpass
    request per category in `FEATURES`, each answered with `out count` --
    counting server-side rather than downloading the geometry, because the
    question is "how many hospitals" and not "which ones".

    `timeout` is what Overpass is allowed to spend per query; `deadline_s` is
    what this function is allowed to spend in total. The second one matters:
    six categories over three mirrors with nothing else stopping them is half
    an hour, and nothing above a workflow interrupts one -- `core/monty_graph.py`
    runs a task to completion with no wall clock around it. So the ceiling
    lives here, and categories past it are reported as not attempted.

    Returns `{"ok", "counts", "missing", "area_km2", "source", "problem"}`.
    Fails soft, like everything else in this module: exposure counts are
    context for an analysis, not a precondition of one. `ok` is True when
    *any* category answered, so read `missing` before reading `counts`.
    """
    if not bbox or len(list(bbox)) != 4:
        return {"ok": False, "counts": {}, "source": OVERPASS_SOURCE,
                "problem": "bbox must be [west, south, east, north]"}

    west, south, east, north = (float(v) for v in bbox)
    area = bbox_km2(bbox)
    if area > MAX_AOI_KM2:
        return {"ok": False, "counts": {}, "source": OVERPASS_SOURCE,
                "area_km2": round(area, 1),
                "problem": f"AOI is {area:,.0f} km2, over the {MAX_AOI_KM2:,} km2 "
                           "ceiling for a feature count; narrow the AOI"}

    # Overpass orders a bbox (south, west, north, east). Every other bbox in
    # this repo is (west, south, east, north), so the swap happens once, here.
    box = f"{south},{west},{north},{east}"

    # One request per category rather than one request with six named sets.
    # Six in one is a single query heavy enough that the public server answers
    # 504 on a city the size of Derna, and a 504 there costs every count. Split
    # up, a category that times out costs only itself: the hospitals still
    # arrive when the unqualified building scan does not.
    counts: dict[str, int] = {}
    problems: list[str] = []
    started = time.monotonic()
    for index, (key, (selector, kinds)) in enumerate(FEATURES.items()):
        if time.monotonic() - started > deadline_s:
            problems.append(f"{key}: not attempted, {deadline_s:.0f}s budget spent")
            continue
        clauses = "".join(f"{kind}{selector}({box});" for kind in kinds)
        query = f"[out:json][timeout:{timeout}];({clauses})->.s;.s out count;"

        # A public instance hands out a small number of query slots per client
        # and answers 429 when they are all busy, or 504 when the query is more
        # than it will spend time on. Both are load, and load passes, so the
        # category waits and asks again. Requests are spaced either way: this
        # is a shared resource with a published etiquette.
        payload, last = None, f"{key}: not attempted"
        for attempt in range(ATTEMPTS):
            if time.monotonic() - started > deadline_s:
                last = f"{key}: {last.split(': ', 1)[-1]} (budget spent, not retried)"
                break
            if index or attempt:
                time.sleep(PACE if not attempt else BUSY_PAUSE)
            request = urllib.request.Request(
                OVERPASS_URLS[attempt % len(OVERPASS_URLS)],
                data=query.encode("utf-8"),
                headers={"User-Agent": USER_AGENT,
                         "Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(request, timeout=timeout + 15) as response:
                    payload = json.loads(response.read().decode("utf-8", "replace"))
                break
            except Exception as exc:                           # noqa: BLE001
                last = f"{key}: {type(exc).__name__}: {exc}"
        if payload is None:
            problems.append(last)
            continue

        tally = next((e for e in (payload.get("elements") or [])
                      if e.get("type") == "count"), None)
        if tally is None:
            problems.append(f"{key}: no count in the reply")
            continue
        counts[key] = int((tally.get("tags") or {}).get("total", 0))

    # A category that failed is absent from `counts`, never present as 0. Zero
    # means "none mapped here", which is a finding; a timeout means "not
    # known", which is not. Collapsing the two would put a confident nought in
    # front of a reader with no way to tell it was never measured.
    return {"ok": bool(counts), "counts": counts, "area_km2": round(area, 1),
            "missing": [k for k in FEATURES if k not in counts],
            "source": OVERPASS_SOURCE,
            "problem": "; ".join(problems)}
