"""Which NRT flood products have data over an AOI, day by day, for an event.

A quick glance before the suitability assessment: of the five products the
MCDM ranks, which actually observed this place in this window, and how often?
A product can rank first on paper and have no acquisition over the AOI during
the event -- that is worth seeing before reading a ranking.

    data_availability(bbox, start_date, end_date)

**Sources, all searched without an account or a key:**

    GFM (Sentinel-1)  EODC STAC API, collection GFM
    OPERA DSWx-HLS    NASA CMR-STAC (PO.DAAC), OPERA_L3_DSWX-HLS_V1
    OPERA DSWx-S1     NASA CMR-STAC (PO.DAAC), OPERA_L3_DSWX-S1_V1
    MODIS MCDWD       NASA CMR-STAC (LANCE), MCDWD_L3_NRT 6.1
    VIIRS VCDWD       NASA CMR-STAC (LANCE), VCDWD_L3_NRT 2

**An instance is one acquisition, not one file.** A satellite pass is cut into
tiles (MGRS for OPERA, Equi7 for GFM, 10-degree tiles for LANCE), and OPERA
re-publishes reprocessed versions under new names, so a raw file count says
more about tiling than about observation: DSWx-S1 returned 11 files over the
Kelani AOI on one day that held one pass. Files whose sensing times fall within
`GROUP_MINUTES` of each other are one instance; the files and tiles behind it
travel with it.

**MODIS and VIIRS are NRT-only, and their archive rolls.** LANCE keeps the
MCDWD and VCDWD granules for about a week -- CMR and the LANCE archive alike
(measured 2026-10-11: the oldest held was 3 Oct 2026, and a Nov 2025 day was a
404). There is no long-term archive of either. A day older than the oldest
granule held is therefore `not_checkable`, never `none`: a zero there would
read as "no data" when the truth is "no record kept". Both products are
produced DAILY by design, so for those days the useful question is not
whether a granule exists but whether the sensor could see the ground -- and the
answer reports the model cloud cover at each sensor's nominal overpass time
instead (from `fetch_weather`, the model named in the request).

**Days are local to the AOI**, as every daily figure in this repo is
(docs/WORKFLOWS.md). The offset is the AOI centre's civil UTC offset, from
Open-Meteo's timezone lookup; if that fails the solar offset (longitude / 15)
is used and the payload says so.

**A day LANCE no longer holds is counted from GIBS** (BUG-030; user decision
2026-11-12). The NASA GIBS imagery archive keeps the MCDWD and VCDWD daily
composites for years, and get_flood_map reads them from there. Each such day's
1-Day composite is read over the AOI: held and seeing any ground ->
`available` with count 1 (one daily composite -- GIBS merges the day's passes
and cannot say how many there were); all cloud, or nothing over the AOI ->
`none`, 0 (`all_cloud` marks the first); a failed read -> `unreachable`. Only a day no
archive holds is `not_checkable` ("no data" on the chart). It used to be
`in_gibs` with 0 passes, which read as "no data" on a day the archive held.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import workflow
from ._mcdm_data import ALTERNATIVE_IDS, ALTERNATIVES

TIMEOUT = 60
MAX_DAYS = 62
MAX_PAGES = 20
GROUP_MINUTES = 10
USER_AGENT = "pralai-flood-research/0.1 (research; contact via repo)"

CMR_SEARCH = "https://cmr.earthdata.nasa.gov/search/granules.json"
# The FORECAST endpoint, not the archive: the archive does not serve today, and
# a time-zone lookup needs only the zone, which either endpoint returns.
OFFSET_LOOKUP = "https://api.open-meteo.com/v1/forecast"

# Nominal equator-crossing times, local SOLAR time, of the sensors behind the
# two daily optical products. Nominal: Terra and Aqua have drifted since
# 2022-23, and an AOI off the sub-satellite track is seen earlier or later.
# Close enough to say "the sky at the time the sensor passed", which a 24-hour
# mean -- half of it night -- is not.
OVERPASS_SOLAR_H = {"Terra": 10.5, "Aqua": 13.5, "VIIRS": 13.5}

# The five products, in the MCDM's own order. `rolling` marks an NRT-only
# product whose archive keeps about a week; `overpass` names the sensors whose
# cloud is reported for it.
PRODUCTS: list[dict[str, Any]] = [
    {"id": "gfm", "stac": "https://stac.eodc.eu/api/v1", "collection": "GFM",
     "provider": "EODC (Copernicus EMS / GloFAS Global Flood Monitoring)"},
    {"id": "dswx_hls", "stac": "https://cmr.earthdata.nasa.gov/stac/POCLOUD",
     "collection": "OPERA_L3_DSWX-HLS_V1_1.0", "provider": "NASA PO.DAAC via CMR-STAC"},
    {"id": "dswx_s1", "stac": "https://cmr.earthdata.nasa.gov/stac/POCLOUD",
     "collection": "OPERA_L3_DSWX-S1_V1_1.0", "provider": "NASA PO.DAAC via CMR-STAC"},
    {"id": "mcdwd", "stac": "https://cmr.earthdata.nasa.gov/stac/LANCEMODIS",
     "collection": "MCDWD_L3_NRT_6.1", "provider": "NASA LANCE via CMR-STAC",
     "rolling": {"short_name": "MCDWD_L3_NRT", "version": "6.1"},
     "gibs_layer": "MODIS_Combined_Flood_1-Day",
     "overpass": ["Terra", "Aqua"]},
    {"id": "vcdwd", "stac": "https://cmr.earthdata.nasa.gov/stac/LANCEMODIS",
     "collection": "VCDWD_L3_NRT_2", "provider": "NASA LANCE via CMR-STAC",
     "rolling": {"short_name": "VCDWD_L3_NRT", "version": "2"},
     "gibs_layer": "VIIRS_Combined_Flood_1-Day",
     "overpass": ["VIIRS"]},
]
_NAMES = {a["id"]: a for a in ALTERNATIVES}

# The products here and the alternatives the MCDM ranks must be the same five,
# or this chart describes a different decision from the one it sits beside.
if tuple(p["id"] for p in PRODUCTS) != ALTERNATIVE_IDS:
    raise ImportError(f"availability products {[p['id'] for p in PRODUCTS]} do not "
                      f"match the MCDM alternatives {list(ALTERNATIVE_IDS)}")

SOURCES = [
    {"name": "EODC STAC API (GFM)", "url": "https://stac.eodc.eu/api/v1/collections/GFM"},
    {"name": "NASA CMR-STAC, PO.DAAC (OPERA DSWx-HLS, DSWx-S1)",
     "url": "https://cmr.earthdata.nasa.gov/stac/POCLOUD"},
    {"name": "NASA CMR-STAC, LANCE (MCDWD, VCDWD)",
     "url": "https://cmr.earthdata.nasa.gov/stac/LANCEMODIS"},
    {"name": "NASA CMR search API (LANCE rolling-archive extent)",
     "url": "https://cmr.earthdata.nasa.gov/search/"},
    {"name": "NASA GIBS WMTS (MCDWD / VCDWD 1-Day composites, days LANCE no longer holds)",
     "url": "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml"},
    {"name": "Open-Meteo archive (time zone; overpass cloud via fetch_weather)",
     "url": "https://archive-api.open-meteo.com/v1/archive"},
]

INSTANCE = (f"one acquisition: files over the AOI whose sensing times fall within "
            f"{GROUP_MINUTES} minutes of each other (one pass, however many "
            "tiles or reprocessed versions)")
INSTANCE_GIBS = ("MODIS / VIIRS days LANCE no longer holds: the day's NASA GIBS 1-Day "
                 "composite over the AOI, counted 1 when it saw any ground and 0 when "
                 "it saw only cloud (GIBS merges the day's passes)")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _request(url: str, body: dict | None = None) -> dict[str, Any]:
    """One JSON request, retried once on a transient failure.

    CMR answered three of five searches as failures on one run and all five a
    minute later (2026-10-11), with four product searches in flight at once.
    A timeout, a dropped connection, a 429 or a 5xx gets one more attempt after
    a pause; a 4xx is the request's own fault and is raised at once. A second
    failure is raised -- the product is then reported `unreachable` with the
    reason, never as "no data".
    """
    import urllib.error

    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in (1, 2):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if attempt == 2 or not (exc.code == 429 or exc.code >= 500):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
            if attempt == 2:
                raise
        time.sleep(2.5)
    raise RuntimeError("unreachable")                          # pragma: no cover


def stac_search(root: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Every feature a STAC search returns, following `next` links.

    Both STAC APIs here page; CMR-STAC hands back a `next` link even on a
    short page. A next link carrying a body is POSTed with it, one without is
    a plain GET. Bounded by MAX_PAGES so a server that loops cannot hang a run.
    """
    features: list[dict[str, Any]] = []
    page = _request(f"{root}/search", body)
    for _ in range(MAX_PAGES):
        batch = page.get("features") or []
        features.extend(batch)
        link = next((l for l in page.get("links") or [] if l.get("rel") == "next"), None)
        if not batch or not link:
            break
        if link.get("method", "GET").upper() == "POST" or link.get("body"):
            page = _request(link["href"], {**body, **(link.get("body") or {})})
        else:
            page = _request(link["href"])
    return features


def collection_start(root: str, collection: str) -> date | None:
    """The first day a product exists, from its STAC collection extent."""
    meta = _request(f"{root}/collections/{urllib.parse.quote(collection)}")
    try:
        first = meta["extent"]["temporal"]["interval"][0][0]
    except (KeyError, IndexError, TypeError):
        return None
    return _parse_utc(first).date() if first else None


def rolling_start(short_name: str, version: str) -> date | None:
    """The oldest granule LANCE still holds, from the CMR search API."""
    query = urllib.parse.urlencode({"short_name": short_name, "version": version,
                                    "sort_key": "start_date", "page_size": 1})
    entries = (_request(f"{CMR_SEARCH}?{query}").get("feed") or {}).get("entry") or []
    return _parse_utc(entries[0]["time_start"]).date() if entries else None


def zone_lookup(lat: float, lon: float) -> tuple[str | None, str]:
    """`(IANA zone, how)` for a point, or `(None, why)` if the lookup fails.

    Only the zone NAME is taken from Open-Meteo. Its `utc_offset_seconds` is
    the offset in force TODAY, not on the event's dates: after UK summer time
    ended on 2026-10-25 it put every August pass over Edinburgh an hour out
    (BUG-017). The offset is computed from the zone for each timestamp.
    """
    query = urllib.parse.urlencode({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
                                    "forecast_days": 1, "daily": "temperature_2m_max",
                                    "timezone": "auto"})
    try:
        meta = _request(f"{OFFSET_LOOKUP}?{query}")
        zone = meta.get("timezone") or None
        return zone, ("civil, IANA zone from the Open-Meteo timezone lookup; offset "
                      "per timestamp from the tz database")
    except Exception as exc:                                   # noqa: BLE001
        return None, f"the timezone lookup failed: {type(exc).__name__}"


def utc_offset(lat: float, lon: float, on: date | None = None) -> tuple[float, str, str]:
    """`(hours ON THE DATE `on`, zone, how)` for the AOI centre: civil, or
    solar if the lookup fails -- and the payload says which."""
    from ..dating import offset_on

    zone, how = zone_lookup(lat, lon)
    hours = offset_on(zone, on or datetime.now(timezone.utc).date()) if zone else None
    if hours is None:
        return (round(lon / 15.0 * 2) / 2, zone or "",
                f"solar (longitude / 15) -- {how if not zone else 'zone not in the tz database'}")
    return hours, zone, how


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------
def _parse_utc(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(value)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _tile(item_id: str) -> str:
    """The tile a file covers, read from its name: `_T44NMN_` for OPERA, the
    last token (`E024N003T3`) for GFM, `h25v08` for LANCE. Falls back to the
    id, so an unparsed name still counts as its own tile."""
    for pattern in (r"_T(\d{2}[A-Z]{3})_", r"\.(h\d{2}v\d{2})\.", r"_([EW]\d{3}[NS]\d{3}T\d)$"):
        found = re.search(pattern, item_id)
        if found:
            return found[1]
    return item_id


def acquisitions(features: list[dict[str, Any]], offset_h: float,
                 zone: str | None = None) -> list[dict[str, Any]]:
    """Group a product's files into acquisitions, each dated in local time.

    With `zone`, each pass gets the offset in force at ITS OWN time, so a
    window across a clock change dates both sides correctly.

    Sorted by sensing time; a file within GROUP_MINUTES of the previous one
    joins its acquisition. One pass therefore counts once however many tiles
    or reprocessed versions it was published as.
    """
    stamped = []
    for feature in features:
        when = (feature.get("properties") or {}).get("datetime") \
            or (feature.get("properties") or {}).get("start_datetime")
        if when:
            stamped.append((_parse_utc(when), feature.get("id", "")))
    stamped.sort()

    groups: list[dict[str, Any]] = []
    for when, item_id in stamped:
        if groups and when - groups[-1]["_last"] <= timedelta(minutes=GROUP_MINUTES):
            group = groups[-1]
        else:
            group = {"_first": when, "_last": when, "ids": [], "tiles": set()}
            groups.append(group)
        group["_last"] = when
        group["ids"].append(item_id)
        group["tiles"].add(_tile(item_id))

    out = []
    from ..dating import offset_hours

    for group in groups:
        here = offset_hours(zone, group["_first"]) if zone else None
        local = group["_first"] + timedelta(hours=offset_h if here is None else here)
        out.append({
            "utc": group["_first"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "local_date": local.date().isoformat(),
            "local_time": local.strftime("%H:%M"),
            "files": len(group["ids"]),
            "tiles": sorted(group["tiles"]),
            "ids": group["ids"][:10],
        })
    return out


# ---------------------------------------------------------------------------
# Cloud at the overpass, for the two daily optical products
# ---------------------------------------------------------------------------
def overpass_cloud(weather: dict[str, Any], days: list[date],
                   lon: float) -> dict[str, dict[str, float | None]]:
    """AOI-mean model cloud cover at each sensor's nominal overpass, per day.

    The overpass is a local SOLAR time; its UTC instant is solar time minus
    longitude / 15. The two hourly values either side are interpolated
    linearly. A missing hour gives None -- withheld, never filled.
    """
    hourly = weather.get("hourly") or {}
    series = ((hourly.get("vars") or {}).get("cloud_cover_model") or {}).get("mean") or []
    index = {stamp: i for i, stamp in enumerate(hourly.get("time") or [])}

    def at(instant: datetime) -> float | None:
        floor = instant.replace(minute=0, second=0, microsecond=0)
        frac = (instant - floor).total_seconds() / 3600.0
        a = index.get(floor.strftime("%Y-%m-%dT%H:%MZ"))
        b = index.get((floor + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%MZ"))
        if a is None or b is None or a >= len(series) or b >= len(series):
            return None
        va, vb = series[a], series[b]
        if va is None or vb is None:
            return None
        return round(va + (vb - va) * frac, 1)

    out: dict[str, dict[str, float | None]] = {}
    for day in days:
        midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        out[day.isoformat()] = {
            sensor: at(midnight + timedelta(hours=solar - lon / 15.0))
            for sensor, solar in OVERPASS_SOLAR_H.items()
        }
    return out


def _fetch_cloud(bbox: list[float], days: list[date]) -> tuple[dict, dict[str, Any]]:
    """Cloud per day per sensor, and a note on where it came from or why not."""
    from .weather_grid import fetch_weather

    today_utc = datetime.now(timezone.utc).date()
    start, end = days[0] - timedelta(days=1), min(days[-1] + timedelta(days=1), today_utc)
    if end < start:
        return {}, {"unavailable": "the whole window is in the future"}
    # One retry. Open-Meteo has answered this call with an empty body while
    # other requests were in flight (2026-10-11), and the same call succeeded
    # on its own a minute later. A second failure is reported, not hidden.
    weather, error = None, None
    for attempt in (1, 2):
        try:
            weather = fetch_weather(bbox=bbox, start_date=start.isoformat(),
                                         end_date=end.isoformat(), antecedent_days=0)
            break
        except Exception as exc:                               # noqa: BLE001
            error = exc
            if attempt == 1:
                time.sleep(3)
    if weather is None:
        return {}, {"unavailable": f"{type(error).__name__}: {error}"[:300]}
    source = (weather.get("meta") or {}).get("source") or {}
    lon = (bbox[0] + bbox[2]) / 2
    return overpass_cloud(weather, days, lon), {
        "what": "AOI-mean model cloud cover (%) at each sensor's nominal "
                "overpass, interpolated between hours",
        "overpass_local_solar_h": OVERPASS_SOLAR_H,
        "model": source.get("model", ""),
        "retrieved_at": source.get("retrieved_at", ""),
        "workflow": "fetch_weather",
        "caveat": "Model cloud on a ~9 km grid, not the sensor's own cloud "
                  "mask. Overpass times are nominal; Terra and Aqua have "
                  "drifted, and off-nadir views are earlier or later.",
    }


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------
def _check_product(product: dict[str, Any], bbox: list[float], days: list[date],
                   offset_h: float, zone: str | None = None,
                   offset_end_h: float | None = None) -> dict[str, Any]:
    """One product's day-by-day availability. Raises on a failed search."""
    end_h = offset_h if offset_end_h is None else offset_end_h
    window_utc = (
        (datetime(days[0].year, days[0].month, days[0].day, tzinfo=timezone.utc)
         - timedelta(hours=offset_h)),
        (datetime(days[-1].year, days[-1].month, days[-1].day, 23, 59, 59,
                  tzinfo=timezone.utc) - timedelta(hours=end_h)),
    )
    body = {"collections": [product["collection"]], "bbox": bbox, "limit": 250,
            "datetime": f"{window_utc[0]:%Y-%m-%dT%H:%M:%SZ}/{window_utc[1]:%Y-%m-%dT%H:%M:%SZ}"}

    first_day = collection_start(product["stac"], product["collection"])
    held_from = (rolling_start(**product["rolling"]) if product.get("rolling") else None)
    found = acquisitions(stac_search(product["stac"], body), offset_h, zone)

    per_day: dict[str, dict[str, Any]] = {}
    for day in days:
        key = day.isoformat()
        acq = [a for a in found if a["local_date"] == key]
        if acq:
            status = "available"
        elif first_day and day < first_day:
            status = "not_produced"
        elif product.get("rolling") and (held_from is None or day < held_from):
            status = "not_checkable"
        else:
            status = "none"
        per_day[key] = {"status": status, "count": len(acq), "acquisitions": acq}

    return {
        "status": "searched", "query": {"stac": product["stac"], **body},
        "collection_start": first_day.isoformat() if first_day else None,
        "archive_held_from": held_from.isoformat() if held_from else None,
        "days": per_day,
    }


def _gibs_read(layer: str, day: date, bbox: list[float], tm: dict,
               rgb2val: dict) -> dict[str, Any]:
    """One day's GIBS 1-Day composite over the AOI: does it hold the product?

    Classes 0-3 are an observation and 255 is the product saying "insufficient
    data" (cloud, or no clear view) -- both are the product, so both mean the
    day's composite is there. 252-254 are no product: no tile, GIBS no-data,
    a colour outside the colormap. A tile GIBS answers 404 for is not held; any
    other failure is a failed read, never "no data".
    """
    import numpy as np

    from . import gibs

    try:
        arr, _, problems = gibs.fetch(layer, day, bbox, tm, rgb2val)
    except Exception as exc:                                   # noqa: BLE001
        return {"read": "failed", "why": f"{type(exc).__name__}: {exc}"[:200]}
    product = np.isin(arr, (0, 1, 2, 3, 255))
    observed = np.isin(arr, (0, 1, 2, 3))
    if product.any():
        return {"read": "held", "pixels": int(product.sum()),
                "observed_fraction": round(float(observed.sum()) / int(product.sum()), 4)}
    if problems and not all("404" in p for p in problems):
        return {"read": "failed", "why": "; ".join(problems)[:200]}
    return {"read": "empty"}


def _gibs_days(products: list[dict[str, Any]], bbox: list[float],
               offset_h: float = 0.0) -> dict[str, Any]:
    """Count the days LANCE no longer holds from the NASA GIBS archive (BUG-030;
    user decision 2026-11-12: counted like the other products, not as "G").

    For each not_checkable day inside the GIBS layer's time ranges, the day's
    1-Day composite is READ over the AOI. Held and seeing any ground ->
    `available`, count 1 (one daily composite: GIBS merges the day's passes,
    so it cannot say how many there were); held but all cloud ("insufficient
    data" everywhere) -> `none`, count 0, `all_cloud` (user decision
    2026-11-12); read but empty over the AOI -> `none`, count 0; a failed
    read -> `unreachable`. A day outside GIBS's ranges stays `not_checkable` (no
    archive holds it). A failed capabilities read changes nothing and says so.
    """
    from . import gibs

    wanted = [(p, spec["gibs_layer"]) for p, spec in zip(products, PRODUCTS)
              if spec.get("gibs_layer")
              and any(d["status"] == "not_checkable" for d in p["days"].values())]
    if not wanted:
        return {"checked": False, "why": "no day needed it"}
    try:
        caps = gibs._get(gibs.CAPS).decode("utf-8", errors="ignore")
        tm, rgb2val = gibs.finest_matrix(caps), gibs.colormap()
    except Exception as exc:                                   # noqa: BLE001
        return {"checked": False, "why": f"GIBS capabilities unreachable: "
                                         f"{type(exc).__name__}: {exc}"[:200]}
    layers, jobs = {}, []
    for product, layer in wanted:
        ranges = gibs.layer_range(caps, layer)
        layers[layer] = ranges
        product["gibs_layer"] = layer
        jobs += [(product, layer, key) for key, entry in product["days"].items()
                 if entry["status"] == "not_checkable"
                 and gibs.holds(ranges, date.fromisoformat(key))]
    # A handful of small tiles per day; four at a time keeps GIBS unbothered.
    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(lambda j: _gibs_read(j[1], date.fromisoformat(j[2]), bbox,
                                                   tm, rgb2val), jobs))
    for (product, layer, key), got in zip(jobs, reads):
        entry = product["days"][key]
        entry.update({"source": "gibs", "gibs_layer": layer})
        if got["read"] == "held" and not got["observed_fraction"]:
            # Held, but every product pixel over the AOI is "insufficient
            # data" (cloud): the sensor saw no ground, so the day is 0
            # (user decision 2026-11-12). Kept apart from "nothing held".
            entry.update({"status": "none", "count": 0, "all_cloud": True,
                          "observed_fraction": 0.0,
                          "gibs_note": "the day's composite is held, but it saw no ground "
                                       "over the AOI (all 'insufficient data', cloud)"})
        elif got["read"] == "held":
            entry.update({"status": "available", "count": 1, "acquisitions": [{
                "local_date": key, "utc_day": key, "composite": "1-Day",
                "gibs_layer": layer, "files": 1, "tiles": ["GIBS 250m"],
                "observed_fraction": got["observed_fraction"]}]})
        elif got["read"] == "empty":
            entry.update({"status": "none", "count": 0,
                          "gibs_note": "GIBS lists the day but its composite holds no "
                                       "product pixel over the AOI"})
        else:
            entry.update({"status": "unreachable", "count": 0, "gibs_note": got["why"]})
    return {"checked": True, "capabilities": gibs.CAPS, "layers": layers,
            "days_read": len(jobs),
            "caveat": "A GIBS composite is dated by the UTC day; MODIS and VIIRS pass "
                      "in local daytime, so over most AOIs the UTC day is the local day."
                      + (" Here the AOI is far from UTC, so a pass may fall on the next "
                         "or previous local day." if abs(offset_h) >= 10 else "")}


@workflow(
    "data_availability",
    "Which of the five NRT flood products (GFM, OPERA DSWx-HLS, OPERA DSWx-S1, "
    "MODIS MCDWD, VIIRS VCDWD) have acquisitions over a bounding box on each "
    "day of an event window, searched in their STAC catalogues. An instance is "
    "one acquisition (one pass), not one file. MODIS and VIIRS keep only about "
    "a week of NRT archive; older days are read from the NASA GIBS daily "
    "composites (1 when the day's composite saw ground, 0 when it saw only "
    "cloud or holds nothing), and a day no archive holds is "
    "not_checkable with the model cloud cover at its overpass. Draws a "
    "product-by-day chart. "
    "Dates are local to the AOI; at most 62 days.",
    label="NRT product availability",
    source="EODC STAC (GFM); NASA CMR-STAC (OPERA, LANCE); NASA GIBS (MODIS, VIIRS older days); Open-Meteo (cloud)",
    order=15,
    data_source_uncertainty=(
        "The catalogues list what was published, not what was usable. MODIS and "
        "VIIRS NRT keep only about a week; older days come from the GIBS daily "
        "composite, which merges a day's passes (one per day, not a pass count), "
        "and their cloud at overpass is modelled (IFS)."),
    method_uncertainty=(
        "Files within a few minutes of each other count as one pass, and days are "
        "local to the AOI from a timezone lookup with a solar-offset fallback; a "
        "wrong grouping or offset can move a pass to the wrong day."),
)
def data_availability(
    bbox: list[float],
    start_date: str,
    end_date: str,
    event_id: str = "",
    output_dir: str = "outputs",
    write_figure: bool = True,
    include_cloud: bool = True,
) -> dict[str, Any]:
    """Day-by-day availability of the five NRT flood products over an AOI.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.
        start_date, end_date: ISO dates, local to the AOI, inclusive.
        event_id: names the output subdirectory; "availability" when empty.
        output_dir: where the chart goes (default `outputs`).
        write_figure: false to return the counts without drawing the chart.
        include_cloud: false to skip the overpass-cloud lookup.
    """
    if len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    west, south, east, north = (float(v) for v in bbox)
    if west >= east or south >= north:
        raise ValueError(f"bbox is not west<east, south<north: {bbox}")
    try:
        first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError(f"start_date/end_date must be ISO dates: {exc}") from exc
    if last < first:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")
    span = (last - first).days + 1
    if span > MAX_DAYS:
        raise ValueError(f"{span} days requested; at most {MAX_DAYS} fit on one "
                         "chart a reader can scan. Split the window")
    days = [first + timedelta(days=i) for i in range(span)]
    bbox = [west, south, east, north]
    lat, lon = (south + north) / 2, (west + east) / 2

    # The offset ON THE WINDOW'S DATES (BUG-017), at both ends: a window can
    # cross a clock change, and each pass is then dated with its own offset.
    offset_h, zone, offset_how = utc_offset(lat, lon, first)
    from ..dating import offset_on

    offset_end_h = offset_on(zone, last) if zone else None
    offset_end_h = offset_h if offset_end_h is None else offset_end_h

    def run(product: dict[str, Any]) -> dict[str, Any]:
        base = {"id": product["id"], "label": _NAMES[product["id"]]["name"],
                "sensor": _NAMES[product["id"]]["sensor"],
                "provider": product["provider"], "collection": product["collection"],
                "rolling_archive": bool(product.get("rolling"))}
        try:
            return {**base, **_check_product(product, bbox, days, offset_h, zone or None,
                                             offset_end_h)}
        except Exception as exc:                               # noqa: BLE001
            return {**base, "status": "unreachable",
                    "note": f"{type(exc).__name__}: {exc}"[:300],
                    "days": {d.isoformat(): {"status": "unreachable", "count": 0,
                                             "acquisitions": []} for d in days}}

    with ThreadPoolExecutor(max_workers=len(PRODUCTS)) as pool:
        products = list(pool.map(run, PRODUCTS))
    gibs_note = _gibs_days(products, bbox, offset_h)

    cloud_note: dict[str, Any] = {"skipped": "include_cloud=false"}
    if include_cloud:
        cloud, cloud_note = _fetch_cloud(bbox, days)
        for product, spec in zip(products, PRODUCTS):
            for sensor in spec.get("overpass") or []:
                for key, entry in product["days"].items():
                    entry.setdefault("cloud_pct", {})[sensor] = (cloud.get(key) or {}).get(sensor)

    for product in products:
        counts = [d["count"] for d in product["days"].values()]
        statuses = [d["status"] for d in product["days"].values()]
        product["totals"] = {
            "instances": sum(counts),
            "days_available": statuses.count("available"),
            "days_none": statuses.count("none"),
            "days_not_checkable": statuses.count("not_checkable"),
            # Days counted from the GIBS archive (BUG-030); `in_gibs` is the
            # status an older payload carries for them.
            "days_in_gibs": sum(1 for d in product["days"].values()
                                if d.get("source") == "gibs" or d["status"] == "in_gibs"),
            "days_all_cloud": sum(1 for d in product["days"].values() if d.get("all_cloud")),
            "days_not_produced": statuses.count("not_produced"),
        }
        if product.get("rolling_archive") and product["totals"]["days_in_gibs"]:
            product["gibs_note"] = (
                f"{product['totals']['days_in_gibs']} day(s) no longer in LANCE were read from "
                f"the NASA GIBS imagery archive ({product.get('gibs_layer')}): one daily "
                "composite per day (the day's passes merged, so not a pass count), the "
                "product's classes re-gridded by GIBS; get_flood_map reads them there. "
                f"{product['totals']['days_all_cloud']} of them saw no ground over the AOI "
                "(all cloud) and count 0.")
        if product.get("rolling_archive") and product["totals"]["days_not_checkable"]:
            product["note"] = (
                "NRT-only product, produced daily for every land tile; LANCE "
                f"keeps about a week (oldest held: {product.get('archive_held_from')}), "
                "and the GIBS archive does not hold these days either: they cannot be "
                "checked -- see cloud_pct for whether the sensor could have seen the ground.")

    payload: dict[str, Any] = {
        "aoi": {"bbox": bbox},
        "period": {"start": first.isoformat(), "end": last.isoformat(),
                   "days": [d.isoformat() for d in days],
                   "utc_offset_h": offset_h, "timezone": zone,
                   "offset_source": offset_how,
                   **({"utc_offset_end_h": offset_end_h,
                       "clock_change": f"the clock changes inside the window: UTC"
                                       f"{offset_h:+g} h at the start, UTC{offset_end_h:+g} h "
                                       "at the end; each pass is dated with its own offset"}
                      if offset_end_h != offset_h else {})},
        "instance_definition": INSTANCE,
        "instance_definition_gibs": INSTANCE_GIBS,
        "status_meaning": {
            "available": "one or more acquisitions over the AOI that day (MODIS / VIIRS "
                         "days read from GIBS: the day's composite saw ground, counted 1)",
            "none": "searched; no acquisition over the AOI that day (MODIS / VIIRS days "
                    "read from GIBS: no composite, or one that saw only cloud -- all_cloud)",
            "not_checkable": "no data: the product's rolling NRT archive no longer holds "
                             "that day, and the GIBS imagery archive does not either",
            "in_gibs": "older payloads only: the day was in the GIBS archive, not counted",
            "not_produced": "before the product's first day",
            "unreachable": "the catalogue search (or the GIBS read) failed",
        },
        "products": products,
        "cloud": cloud_note,
        "gibs": gibs_note,
        "sources": SOURCES,
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if write_figure:
        from .. import charts

        png = Path(output_dir) / (event_id or "availability") / "data_availability.png"
        charts.availability(payload, png)
        # `figure` is what core/figure_refs.register_run looks for: the chart
        # becomes `<image - run-region-year-data_availability>` for Argus.
        payload["figure"] = {"name": "data_availability", "path": str(png),
                             "caption": "Day-by-day availability of the five NRT "
                                        "flood products over the AOI."}
    return payload
