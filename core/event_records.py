"""What the disaster registers hold for an event: names and links, nothing else.

Runs when the resolver reads a message as asking about the event itself
(`about_event`). Seven sources, checked in parallel:

    GDACS                          JRC / UN OCHA alert register     searched (API)
    Copernicus EMS                 Rapid Mapping activations         searched (public list)
    UNOSAT                         UNITAR maps, via HDX              searched (CKAN API)
    ZKI (DLR)                      Center for Satellite Based        searched (list page)
                                   Crisis Information
    Dartmouth Flood Observatory                                      catalogue link only
    NASA Disasters                 NASA Disasters Program portal     catalogue link only
    NASA NRT Global Flood Product  LANCE MODIS/VIIRS                 catalogue link only

**The answer gets names and links, assembled in Python.** No model reads or
restates these: a link a model paraphrased is a link nobody can trust, and the
user asked for the links, not a summary of them. `render()` writes the section
that is appended under Argus' answer.

**Every source reports its own status**, and a source that could not be
searched says so rather than looking like "nothing found":

    matched     the register holds records for this country and period
    none        searched; nothing for this country and period
    catalogue   there is no event search to query; the link is the catalogue
    unreachable the request failed; the link is the catalogue
    skipped     a required input (the country, the window) is missing

Matching is by country and date -- that is what the registers index by. A
match is therefore "recorded in this country in this period", not "this exact
event", and the section says so.
"""

from __future__ import annotations

import html
import json
import re
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Callable

import certifi

from . import places

TIMEOUT = 20
MAX_LINKS = 5
# certifi's bundle, not the Windows store: see the TLS note in
# core/workflows/_fetch.py (the duplicate USERTrust certificate).
TLS = ssl.create_default_context(cafile=certifi.where())

# How far either side of the window a record still counts. Mapping starts after
# the event and runs on for weeks; an event begins before the window a user
# remembers.
BEFORE_DAYS, AFTER_DAYS = 10, 30

CEMS_LIST = ("https://rapidmapping.emergency.copernicus.eu/backend/"
             "dashboard-api/public-activations-info/?limit=1000")
CEMS_PAGE = "https://mapping.emergency.copernicus.eu/activations/{code}/"
HDX_SEARCH = "https://data.humdata.org/api/3/action/package_search"
HDX_PAGE = "https://data.humdata.org/dataset/{name}"
ZKI_LIST = "https://activations.zki.dlr.de/en/activations/"
ZKI_BASE = "https://activations.zki.dlr.de"

GDACS = "GDACS"
CEMS = "Copernicus EMS"
UNOSAT = "UNOSAT"
ZKI = "ZKI (DLR)"
DFO = "Dartmouth Flood Observatory"
NASA = "NASA Disasters"
NASA_FLOOD = "NASA NRT Global Flood Product"

CATALOGUES = {
    GDACS: "https://www.gdacs.org/Alerts/default.aspx",
    CEMS: "https://mapping.emergency.copernicus.eu/activations/",
    UNOSAT: "https://unosat.org/products/",
    ZKI: ZKI_LIST,
    DFO: "https://floodobservatory.colorado.edu/Archives/index.html",
    NASA: "https://disasters.nasa.gov/",
    NASA_FLOOD: ("https://www.earthdata.nasa.gov/data/instruments/viirs/"
                 "near-real-time-data/nrt-global-flood-products"),
}


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": places.USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=TLS) as response:
        return response.read().decode("utf-8", "replace")


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def span_of(start: str, end: str) -> tuple[date, date] | None:
    """The padded search span, or None when the session has no window."""
    first, last = _day(start), _day(end) or _day(start)
    if not first:
        return None
    return first - timedelta(days=BEFORE_DAYS), last + timedelta(days=AFTER_DAYS)


def _in(day: date | None, span: tuple[date, date] | None) -> bool:
    return day is not None and span is not None and span[0] <= day <= span[1]


def _result(source: str, status: str, links: list[dict] | None = None,
            note: str = "") -> dict[str, Any]:
    links = links or []
    return {"source": source, "status": status, "links": links[:MAX_LINKS],
            "more": max(0, len(links) - MAX_LINKS),
            "catalogue": CATALOGUES[source], "note": note}


def _link(title: str, url: str, when: str = "") -> dict[str, str]:
    return {"title": " ".join(title.split()), "url": url, "date": when[:10]}


# ---------------------------------------------------------------------------
# The searchable sources. Each takes the country, the padded span and the
# bbox; each raises on a network failure, which `lookup` reports as
# `unreachable`.
# ---------------------------------------------------------------------------
def gdacs(country: str, span, bbox=None) -> dict[str, Any]:
    if not country:
        return _result(GDACS, "skipped", note="no country for the AOI")
    found = places.gdacs_events(country, span[0].isoformat(), span[1].isoformat())
    if not found.get("events"):
        problem = found.get("problem", "")
        if problem.startswith("no "):
            return _result(GDACS, "none")
        return _result(GDACS, "unreachable", note=problem[:160])
    links = [_link(f"{e['name']} ({e['kind']}, {e['start']} to {e['end']})",
                   e["url"] or CATALOGUES[GDACS], e["start"])
             for e in found["events"]]
    return _result(GDACS, "matched", links)


def cems(country: str, span, bbox=None) -> dict[str, Any]:
    payload = json.loads(_get(CEMS_LIST))
    links = []
    for item in payload.get("results") or []:
        countries = [c.lower() for c in item.get("countries") or []]
        hit = bool(country) and country.lower() in countries
        if not hit and bbox:
            # No country, or a cross-border activation: fall back to the
            # activation's centroid inside the AOI padded by half a degree.
            point = re.match(r"POINT \(([-\d.]+) ([-\d.]+)\)", item.get("centroid") or "")
            if point:
                lon, lat = float(point[1]), float(point[2])
                west, south, east, north = bbox
                hit = (west - 0.5 <= lon <= east + 0.5
                       and south - 0.5 <= lat <= north + 0.5)
        if hit and _in(_day(item.get("eventTime", "")), span):
            links.append(_link(f"{item['code']} -- {item.get('name', '')}",
                               CEMS_PAGE.format(code=item["code"]),
                               item.get("eventTime", "")))
    links.sort(key=lambda l: l["date"], reverse=True)
    return _result(CEMS, "matched" if links else "none", links,
                   note="the public list covers activations from 2023 on")


def unosat(country: str, span, bbox=None) -> dict[str, Any]:
    if not country:
        return _result(UNOSAT, "skipped", note="no country for the AOI")
    query = urllib.parse.urlencode({
        "fq": "organization:unosat", "q": country, "rows": 100,
        "sort": "metadata_created desc"})
    payload = json.loads(_get(f"{HDX_SEARCH}?{query}"))
    links = []
    for item in (payload.get("result") or {}).get("results") or []:
        # dataset_date is "[2025-12-03T00:00:00 TO 2025-12-03T23:59:59]".
        days = [d for d in (_day(x) for x in re.findall(
            r"\d{4}-\d{2}-\d{2}", item.get("dataset_date") or "")) if d]
        if not days:
            continue
        # Full-text search on the country name also hits a neighbour's
        # dataset that mentions it; the dataset's own country groups decide.
        groups = [g.get("display_name", "").lower() for g in item.get("groups") or []]
        if groups and country.lower() not in groups:
            continue
        if days[0] <= span[1] and days[-1] >= span[0]:
            links.append(_link(item.get("title") or item["name"],
                               HDX_PAGE.format(name=item["name"]),
                               days[0].isoformat()))
    return _result(UNOSAT, "matched" if links else "none", links,
                   note="UNOSAT products as published on HDX")


def zki(country: str, span, bbox=None) -> dict[str, Any]:
    if not country:
        return _result(ZKI, "skipped", note="no country for the AOI")
    page = _get(ZKI_LIST)
    links = []
    for block in page.split('<div class="event-item"')[1:]:
        found = re.match(r'\s*data-country="([^"]*)"', block)
        if not found or found[1] != country.lower():
            continue
        href = re.search(r'href="(/en/activations/items/[^"]+)"', block)
        title = re.search(r"<h2>(.*?)</h2>", block, re.S)
        day = re.search(r'<span class="day">([^<]+)</span>', block)
        when = None
        if day:
            try:
                when = datetime.strptime(day[1].strip(), "%d. %B %Y").date()
            except ValueError:
                when = None
        if href and title and _in(when, span):
            links.append(_link(html.unescape(title[1]), ZKI_BASE + href[1],
                               when.isoformat()))
    return _result(ZKI, "matched" if links else "none", links,
                   note="ZKI lists its activations on one page")


# ---------------------------------------------------------------------------
# The catalogue-only sources
# ---------------------------------------------------------------------------
def dfo(country: str = "", span=None, bbox=None) -> dict[str, Any]:
    # No machine-readable event search was found, and the server did not
    # answer at all when this was built (2026-10-10). Checked anyway, so the
    # answer can say whether the archive link is likely to open.
    try:
        _get(CATALOGUES[DFO])
    except Exception as exc:                                   # noqa: BLE001
        return _result(DFO, "unreachable",
                       note=f"{type(exc).__name__}; no event search, archive index only")
    return _result(DFO, "catalogue", note="no event search; archive index only")


def nasa_disasters(country: str = "", span=None, bbox=None) -> dict[str, Any]:
    return _result(NASA, "catalogue",
                   note="no event search; the portal lists events and data")


def nasa_flood_product(country: str = "", span=None, bbox=None) -> dict[str, Any]:
    return _result(NASA_FLOOD, "catalogue",
                   note="daily MODIS/VIIRS flood maps by tile; no event index")


SEARCHED: list[tuple[str, Callable[..., dict]]] = [
    (GDACS, gdacs), (CEMS, cems), (UNOSAT, unosat), (ZKI, zki)]
LINKED: list[tuple[str, Callable[..., dict]]] = [
    (DFO, dfo), (NASA, nasa_disasters), (NASA_FLOOD, nasa_flood_product)]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def lookup(country: str, start: str, end: str,
           bbox: tuple[float, float, float, float] | None = None,
           searched=None, linked=None) -> list[dict]:
    """Ask every source at once. One failing source never stops the others.

    Without a window nothing is searched -- a country's whole history of
    activations is not an answer about one event -- and the searchable
    sources are listed by their catalogue links, marked `skipped`.

    `searched` and `linked` default to the real sources and exist so a test
    can exercise these rules without the network.
    """
    searched = SEARCHED if searched is None else searched
    linked = LINKED if linked is None else linked
    span = span_of(start, end)

    def run(job: tuple[tuple[str, Callable[..., dict]], bool]) -> dict:
        (name, fn), needs_span = job
        if needs_span and span is None:
            return _result(name, "skipped", note="no time window in the session")
        try:
            return fn(country, span, bbox)
        except Exception as exc:                               # noqa: BLE001
            return _result(name, "unreachable",
                           note=f"{type(exc).__name__}: {exc}"[:160])

    jobs = [(j, True) for j in searched] + [(j, False) for j in linked]
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        return list(pool.map(run, jobs))


def for_record(record) -> list[dict]:
    """`lookup` for a session record.

    Fills in the country when the AOI was given as coordinates and so was
    never geocoded -- the Kelani bbox is exactly that case, and every register
    indexes by country.
    """
    country = record.place.country
    bbox = record.place.bbox
    if not country and bbox:
        west, south, east, north = bbox
        country = places.country_at((west + east) / 2, (south + north) / 2)
    return lookup(country, record.window.start, record.window.end, bbox)


def render(records: list[dict], where: str = "", when: str = "") -> str:
    """The section appended under the answer: names and links, as markdown."""
    if not records:
        return ""
    scope = " ".join(x for x in (f"for {where}" if where else "",
                                 f"around {when}" if when else "") if x)
    lines = ["", "---", "",
             f"**Event records{(' ' + scope) if scope else ''}**", "",
             "Matched by country and date, not by event: check that each link "
             "is the event you mean.", ""]
    labels = {"none": "no record for this country and period",
              "catalogue": "catalogue",
              "unreachable": "could not be reached",
              "skipped": "not searched"}
    for rec in records:
        if rec["status"] == "matched":
            lines.append(f"- **{rec['source']}**")
            for link in rec["links"]:
                lines.append(f"  - {link['title']} -- {link['url']}")
            if rec["more"]:
                lines.append(f"  - {rec['more']} more: {rec['catalogue']}")
            continue
        note = f" ({rec['note']})" if rec["note"] and rec["status"] != "none" else ""
        lines.append(f"- **{rec['source']}** -- {labels[rec['status']]}{note}: "
                     f"{rec['catalogue']}")
    return "\n".join(lines)
