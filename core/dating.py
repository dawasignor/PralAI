"""When the data was acquired, said in one form everywhere (user decision 2026-10-26).

Every figure a report, a brief or an answer quotes rests on data acquired at
some time, and the reader needs that date: "Sentinel-2 L2A scene S2A_...,
acquired 11 Aug 2026". This module is the one place that writes it.

**Dates, not times** (user decision 2026-11-01): a readable local date --
"11 Aug 2026", "24-30 Sep 2026" -- wherever a source is attributed. The hour
is shown only where it matters (`stamp(..., time=True)`); the weather says
"(UTC days)" because it is summed by UTC day.

Three rules.

1. **The zone is the AOI's IANA zone, resolved once per event** by
   `data_availability` (the first step of every Flood analyst run) and read
   back from its stored payload, so a later render needs no network. The
   offset is computed FOR THE TIMESTAMP with `zoneinfo`, never taken from the
   day the lookup ran: Open-Meteo's `utc_offset_seconds` is today's offset, and after
   UK summer time ended (2026-10-25) it put every August pass over Edinburgh
   an hour out (BUG-017).
2. **Times are read from the payloads' own fields, never invented.** A static
   product's period (GLO-30's TanDEM-X acquisitions, JRC's 1984-2021 record)
   is the one stated in docs/SOURCES.md. A payload with no time of its own
   says so ("acquisition time not recorded"), it is never given a guess.
3. **The date is the AOI's local date.** With no zone known it is the UTC
   date, marked "(UTC)", rather than a UTC date passed off as local.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

# Static products: the acquisition period docs/SOURCES.md states for each.
STATIC = {
    "terrain": ("Copernicus DEM GLO-30", "TanDEM-X acquisitions 2011-2015 (SOURCES.md)"),
    "permanent_water": ("JRC Global Surface Water occurrence",
                        "Landsat record 1984-2021 (SOURCES.md)"),
}


# Results computed from other results: no acquisition of their own.
DERIVED = {"rank_flood_products", "mcdm", "mcdm_decision_matrix", "sensitivity_analysis",
           "sensitivity",
           "accuracy_metrics"}
# A plain name where the store name would read as a tool identifier.
NAMES = {"where_am_i": "OpenStreetMap place context",
         "assess_road_access": "OpenStreetMap roads", "check_slope_tree_roads":
         "OpenStreetMap roads", "population": "population",
         "search_previous_disasters": "previous disasters",
         "map_hazard_exposure": "OpenStreetMap facilities"}


# -- the zone ----------------------------------------------------------------------
def offset_hours(zone: str | None, when: datetime) -> float | None:
    """The zone's UTC offset IN FORCE AT `when` (aware or UTC-naive)."""
    if not zone:
        return None
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return when.astimezone(tz).utcoffset().total_seconds() / 3600.0


def offset_on(zone: str | None, day: date) -> float | None:
    """The offset at local noon of `day` -- a day's clock, for searches by day."""
    return offset_hours(zone, datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc))


def store_zone(store: Any) -> str | None:
    """The event's zone, as its data-availability check resolved it, or None."""
    for name in ("data_availability", "availability"):
        try:
            rec = store.cached_input(name)
        except Exception:                                       # noqa: BLE001
            rec = None
        zone = (((rec or {}).get("payload") or {}).get("period") or {}).get("timezone")
        if zone:
            return zone
    return None


# -- one timestamp -----------------------------------------------------------------
def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if len(text) == 16 and "T" in text:                 # 2026-08-14T00:00+00:00 short form
        text = text + ":00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        try:
            when = datetime.fromisoformat(text[:19])
        except ValueError:
            return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _local_day(value: Any, zone: str | None) -> date | None:
    """The calendar day of `value` in the AOI's zone; the UTC day without one.
    A bare date is that day everywhere -- read as UTC midnight it would move
    to the day before west of Greenwich."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and len(value.strip()) == 10:
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    when = _parse(value)
    if when is None:
        try:
            return date.fromisoformat(str(value or "")[:10])
        except ValueError:
            return None
    if zone:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            return when.astimezone(ZoneInfo(zone)).date()
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return when.astimezone(timezone.utc).date()


def _readable(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def day(value: Any, zone: str | None) -> str:
    """'11 Aug 2026' -- the local date, or the UTC date marked so when the
    zone is not known (the two can differ by a day)."""
    d = _local_day(value, zone)
    if d is None:
        return str(value)
    return _readable(d) + ("" if zone else " (UTC)")


def days(start: Any, end: Any, zone: str | None) -> str:
    """'24-30 Sep 2026', '24 Sep - 2 Oct 2026', or one day when they agree."""
    a, b = _local_day(start, zone), _local_day(end, zone)
    if a is None or b is None:
        return f"{day(start, zone)} to {day(end, zone)}"
    tail = "" if zone else " (UTC)"
    if a == b:
        return _readable(a) + tail
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}-{b.day} {_MONTHS[b.month - 1]} {b.year}{tail}"
    if a.year == b.year:
        return f"{a.day} {_MONTHS[a.month - 1]} - {_readable(b)}{tail}"
    return f"{_readable(a)} - {_readable(b)}{tail}"


def stamp(value: Any, zone: str | None, *, time: bool = False) -> str:
    """'11 Aug 2026' (user decision 2026-11-01: a date is what a reader
    needs). With `time=True`, for the rare place the hour matters:
    '2026-08-11 11:35 UTC (12:35 BST, UTC+01:00, Europe/London)'."""
    when = _parse(value)
    if when is None:
        return str(value)
    if not time:
        return day(when, zone)
    utc = when.astimezone(timezone.utc)
    head = f"{utc:%Y-%m-%d %H:%M} UTC"
    if not zone:
        return f"{head} (local zone not known)"
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        local = utc.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return f"{head} (local zone {zone} not in the tz database)"
    off = local.utcoffset().total_seconds() / 60
    sign = "+" if off >= 0 else "-"
    off = abs(int(off))
    on = "" if local.date() == utc.date() else f"{local:%Y-%m-%d} "
    abbr = local.tzname() or ""
    abbr = "" if abbr.startswith(("+", "-")) else f"{abbr}, "
    return f"{head} ({on}{local:%H:%M} {abbr}UTC{sign}{off // 60:02d}:{off % 60:02d}, {zone})"


def pass_times(values: list[Any], zone: str | None) -> str:
    """Every acquisition with its local time, grouped by local day (user,
    2026-11-19: "attach the acquisition time in the local time along with the
    date ... time is an important factor").

    '26 Nov 2025 at 05:55 and 18:19; 1 Dec 2025 at 05:55 (local time,
    UTC+05:30, Asia/Colombo)'. Without a zone the clock is UTC, said so.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    tz = None
    if zone:
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            tz = None
    by_day: dict[date, list[str]] = {}
    offset = ""
    for value in values:
        when = _parse(value)
        if when is None:
            continue
        local = when.astimezone(tz) if tz else when.astimezone(timezone.utc)
        by_day.setdefault(local.date(), []).append(f"{local:%H:%M}")
        if tz and not offset:
            off = int(local.utcoffset().total_seconds() // 60)
            sign = "+" if off >= 0 else "-"
            offset = f"UTC{sign}{abs(off) // 60:02d}:{abs(off) % 60:02d}"
    if not by_day:
        return ", ".join(str(v) for v in values)
    parts = []
    for d in sorted(by_day):
        times = sorted(set(by_day[d]))
        clock = times[0] if len(times) == 1 else ", ".join(times[:-1]) + " and " + times[-1]
        parts.append(f"{_readable(d)} at {clock}")
    note = f"local time, {offset}, {zone}" if tz else "UTC; the local time zone is not known"
    return "; ".join(parts) + f" ({note})"


def span(start: Any, end: Any, zone: str | None, *, time: bool = False) -> str:
    if time:
        return f"{stamp(start, zone, time=True)} to {stamp(end, zone, time=True)}"
    return days(start, end, zone)


# -- what each payload was acquired on -----------------------------------------------
def _g(d: Any, path: str, default: Any = None) -> Any:
    for part in path.split("."):
        if not isinstance(d, dict):
            return default
        d = d.get(part)
    return default if d is None else d


def stored_tool(name: str) -> str:
    """A per-product flood map (`flood_map_<id>`) is dated like get_flood_map."""
    return "get_flood_map" if str(name).startswith("flood_map_") else name


def entries(name: str, payload: Any) -> list[dict[str, Any]]:
    """What `payload` (stored under `name`) was acquired on, from its own
    fields. Each entry: {what, kind, at | from+to | note}. Kinds: acquired (one
    instant), passes (several), period (a product's acquisition period),
    model_hours, retrieved (as-of time of a live database)."""
    name = stored_tool(name)
    p = payload if isinstance(payload, dict) else {}
    out: list[dict[str, Any]] = []
    if name in DERIVED:
        return [{"what": name.replace("_", " "), "kind": "derived",
                 "note": "computed from this run's inputs, each dated where it is quoted; no acquisition of its own"}]
    if name == "lulc":
        scene = p.get("scene") or {}
        rng = scene.get("datetime_range") or []
        if scene.get("is_mosaic") and len(rng) == 2:
            out.append({"what": f"Sentinel-2 L2A mosaic {scene.get('id', '')}".strip(),
                        "kind": "passes", "from": rng[0], "to": rng[1]})
        elif scene.get("datetime"):
            out.append({"what": f"Sentinel-2 L2A scene {scene.get('id', '')}".strip(),
                        "kind": "acquired", "at": scene["datetime"]})
    elif name == "worldcover":
        src = p.get("source") or {}
        if src.get("time_start"):
            out.append({"what": src.get("dataset", "ESA WorldCover"), "kind": "period",
                        "from": src["time_start"], "to": src.get("time_end", "")})
    elif name in STATIC:
        what, note = STATIC[name]
        out.append({"what": what, "kind": "period", "note": note})
    elif name in ("weather_grid", "weather"):
        meta = p.get("meta") or {}
        win = meta.get("window_utc") or []
        src = meta.get("source") or {}
        if len(win) == 2:
            from .workflows.weather_grid import model_of

            out.append({"what": f"{model_of(src)['id'] or 'model'} weather, hourly",
                        "kind": "model_hours", "from": win[0], "to": win[1]})
        if src.get("retrieved_at"):
            out.append({"what": "weather archive", "kind": "retrieved", "at": src["retrieved_at"]})
    elif name in ("data_availability", "availability"):
        passes = sorted(a["utc"] for prod in p.get("products") or []
                        for day in (prod.get("days") or {}).values()
                        for a in day.get("acquisitions") or [] if a.get("utc"))
        # MODIS / VIIRS days read from GIBS are daily composites dated by the
        # day, not passes with a time (BUG-030): named apart, never added in.
        composites = sorted(a["utc_day"] for prod in p.get("products") or []
                            for day in (prod.get("days") or {}).values()
                            for a in day.get("acquisitions") or [] if a.get("utc_day"))
        # One line per product, every pass with its local time (2026-11-19).
        for prod in p.get("products") or []:
            mine = sorted(a["utc"] for day in (prod.get("days") or {}).values()
                          for a in day.get("acquisitions") or [] if a.get("utc"))
            if mine:
                out.append({"what": f"{prod.get('label') or prod.get('id')} "
                                    f"({len(mine)} pass{'es' if len(mine) != 1 else ''})",
                            "kind": "passes", "from": mine[0], "to": mine[-1], "list": mine})
        if composites:
            out.append({"what": f"{len(composites)} MODIS / VIIRS daily composites from "
                                "the GIBS archive", "kind": "period",
                        "note": days(composites[0], composites[-1], None)
                                .replace(" (UTC)", "") + " (UTC days)"})
    elif name in ("get_flood_map", "get_flood_extent", "flood_extent"):
        used = [o["observation"] for o in p.get("observations") or []
                if o.get("valid_pixels") and o.get("observation")]
        if used:
            out.append({"what": f"{p.get('label') or p.get('product')} passes with data",
                        "kind": "passes", "from": min(used), "to": max(used),
                        "list": sorted(used)})
    elif name == "calc_flood_depth":
        out.append({"what": "flood depth", "kind": "period",
                    "note": "the flood extent's passes on Copernicus DEM GLO-30 "
                            "(TanDEM-X 2011-2015, SOURCES.md)"})
    if not out or name in ("where_am_i", "assess_road_access", "check_slope_tree_roads",
                           "population", "world_bank_indicators", "inform_risk",
                           "search_previous_disasters", "map_hazard_exposure"):
        for src in p.get("sources") or []:
            if not isinstance(src, dict):
                continue
            if src.get("vintage"):
                out.append({"what": src.get("title") or src.get("id", "source"),
                            "kind": "period", "note": f"vintage {src['vintage']}"})
            if src.get("retrieved_at"):
                out.append({"what": src.get("title") or src.get("id", "source"),
                            "kind": "retrieved", "at": src["retrieved_at"]})
        if p.get("retrieved_at") and not any(e["kind"] == "retrieved" for e in out):
            out.append({"what": NAMES.get(name, name.replace("_", " ")), "kind": "retrieved",
                        "at": p["retrieved_at"]})
    if not out:
        out.append({"what": name.replace("_", " "), "kind": "none",
                    "note": "acquisition time not recorded in the stored result"})
    return out


def line(entry: dict[str, Any], zone: str | None) -> str:
    """One entry as the sentence every output uses."""
    what, kind = entry["what"], entry["kind"]
    if kind == "acquired":
        return f"{what}, acquired {pass_times([entry['at']], zone)}"
    if kind == "passes":
        if entry.get("list"):
            return f"{what}, acquired {pass_times(entry['list'], zone)}"
        return f"{what}, acquired {pass_times([entry['from'], entry['to']], zone)} (first and last)"
    if kind == "model_hours":
        # The one place the clock matters: the weather is summed by UTC day,
        # so the days named are UTC days, said so.
        return f"{what}, {span(entry['from'], entry['to'], None).replace(' (UTC)', '')} (UTC days)"
    if kind == "retrieved":
        return f"{what}, retrieved {stamp(entry['at'], zone)}"
    if kind == "period" and entry.get("from"):
        return (f"{what}, {span(entry['from'], entry.get('to') or entry['from'], zone)} "
                "(a product period, not one pass)")
    return f"{what}: {entry.get('note', '')}".rstrip(": ")


def lines(name: str, payload: Any, zone: str | None) -> list[str]:
    return [line(e, zone) for e in entries(name, payload)]


def merged(used: list[tuple[str, Any]], zone: str | None) -> list[tuple[str, str]]:
    """One line per source (BUG-060): a source's vintage and its retrieval on
    one line, and every retrieval of the same source by several tools on that
    line too. Returns (store name of the first payload that quoted it, text).

    Within one payload, a retrieval that names no other entry (Open-Meteo's
    "weather archive") belongs to the payload's one acquisition line."""
    order: list[str] = []
    groups: dict[str, dict[str, Any]] = {}
    for name, payload in used:
        ents = entries(name, payload)
        main = [e for e in ents if e["kind"] != "retrieved"
                and not str(e.get("note", "")).startswith("vintage ")]
        whats = {e["what"] for e in ents if e["kind"] != "retrieved"}
        for e in ents:
            key = e["what"]
            if e["kind"] == "retrieved" and key not in whats and len(main) == 1:
                key = main[0]["what"]
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"name": name, "main": [], "vintage": [], "retrieved": []}
                order.append(key)
            if e["kind"] == "retrieved":
                g["retrieved"].append(stamp(e["at"], zone))
            elif str(e.get("note", "")).startswith("vintage "):
                g["vintage"].append(e["note"][len("vintage "):])
            else:
                g["main"].append(line(e, zone))
    out = []
    for key in order:
        g = groups[key]
        main = list(dict.fromkeys(g["main"]))
        extra = []
        if g["vintage"]:
            extra.append("vintage " + ", ".join(dict.fromkeys(g["vintage"])))
        if g["retrieved"]:
            extra.append("retrieved " + " and ".join(dict.fromkeys(g["retrieved"])))
        text = "; ".join(main + extra) if main else f"{key}: " + "; ".join(extra)
        out.append((g["name"], text))
    return out


def section(used: list[tuple[str, Any]], zone: str | None) -> list[str]:
    """The 'When the data was acquired' block every module carries."""
    if not used:
        return []
    out = ["", "## When the data was acquired", ""]
    out += [f"- {text}" for _, text in merged(used, zone)]
    if not zone:
        out.append("- The AOI's local time zone is not known for this event; times are UTC.")
    return out


# -- the files the tools write (BUG-020) ---------------------------------------------
# The rule above holds for the products themselves, not only for the text that
# quotes them: an NDVI GeoTIFF opened in QGIS a year from now, or its PNG pasted
# into a chapter, has to say where it came from and when, without the report
# beside it. The GeoTIFF carries GDAL metadata tags; every figure carries one
# visible "Source: ... | dated ..." line read from them (core/figures.py).
TAG_SOURCE = "DATA_SOURCE"          # product, publisher, scene or tile
TAG_DATED = "DATED"                 # the same sentences the reports print
TAG_UTC = "ACQUISITION_UTC"         # machine-readable: one instant, or start/end
TAG_ZONE = "AOI_TIMEZONE"           # IANA zone the local times are in, or "not known"
TAG_TOOL = "WRITTEN_BY_TOOL"

# Plain source names by store name, where the payload's own record is thin.
_SOURCE_NAMES = {
    "lulc": "Sentinel-2 L2A (ESA Copernicus), via the Earth Search STAC catalogue (Element 84, AWS)",
    "terrain": "Copernicus DEM GLO-30 (ESA / Airbus, TanDEM-X)",
    "permanent_water": "JRC Global Surface Water (EC JRC / Google)",
    "worldcover": "ESA WorldCover",
    "calc_flood_depth": "FwDET v2.1 (Cohen et al. 2022) on the flood extent and Copernicus DEM GLO-30",
    "where_am_i": "OpenStreetMap, (c) OpenStreetMap contributors",
}


def source_line(name: str, payload: Any) -> str:
    """Where `payload`'s data came from, in one line, from its own fields."""
    name = stored_tool(name)
    p = payload if isinstance(payload, dict) else {}
    parts: list[str] = []
    src = p.get("source") if isinstance(p.get("source"), dict) else {}
    if name in DERIVED:
        return f"computed by {name.replace('_', ' ')} from this run's stored inputs"
    if name == "lulc":
        scene = p.get("scene") or {}
        parts.append(_SOURCE_NAMES["lulc"])
        if scene.get("id"):
            parts.append(f"{'mosaic' if scene.get('is_mosaic') else 'scene'} {scene['id']}")
        if src.get("collection"):
            parts.append(f"collection {src['collection']}")
    elif name in ("weather_grid", "weather"):
        from .workflows.weather_grid import model_of

        msrc = _g(p, "meta.source", {}) or {}
        parts.append(model_of(msrc)["label"])
        if msrc.get("endpoint"):
            parts.append(f"Open-Meteo archive ({msrc['endpoint']})")
    elif name in ("get_flood_map", "get_flood_extent", "flood_extent"):
        parts.append(str(p.get("label") or p.get("product") or "flood product"))
        parts += [s.get("publisher") for s in p.get("sources") or []
                  if isinstance(s, dict) and s.get("publisher")
                  and s.get("publisher") != "per config"][:1]
    elif src.get("dataset"):
        parts.append(src["dataset"])
        if src.get("url"):
            parts.append(src["url"].split("?")[0])
    elif name in _SOURCE_NAMES:
        parts.append(_SOURCE_NAMES[name])
    if not parts:
        for s in p.get("sources") or []:
            if isinstance(s, dict) and (s.get("title") or s.get("name")):
                if s.get("role", "major") == "major":
                    parts.append(", ".join(x for x in (s.get("title") or s.get("name"),
                                                       s.get("publisher")) if x))
            elif isinstance(s, str):
                parts.append(s)
    return "; ".join(dict.fromkeys(x for x in parts if x)) or "source not recorded in the result"


def _utc_tag(name: str, payload: Any) -> str:
    """The acquisition instant or span, ISO UTC, for software to read."""
    out = []
    for e in entries(name, payload):
        if e["kind"] == "acquired":
            out.append(str(e["at"]))
        elif e["kind"] in ("passes", "model_hours") or (e["kind"] == "period" and e.get("from")):
            out.append(f"{e['from']}/{e.get('to', '')}")
        elif e["kind"] == "period" and e.get("note"):
            out.append(f"product period, no single time: {e['note']}")
    return "; ".join(out) or "not recorded"


def provenance(name: str, payload: Any, zone: str | None) -> dict[str, str]:
    """The tags every raster a tool writes carries."""
    return {TAG_SOURCE: source_line(name, payload),
            TAG_DATED: "; ".join(lines(name, payload, zone)),
            TAG_UTC: _utc_tag(name, payload),
            TAG_ZONE: zone or "not known",
            TAG_TOOL: name}


def raster_paths(payload: Any) -> list[str]:
    """Every local GeoTIFF a result names (not the remote tiles it read)."""
    import os

    found: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif (isinstance(x, str) and x.lower().endswith((".tif", ".tiff"))
              and "://" not in x and os.path.isfile(x)):
            found.append(x)

    walk(payload)
    return list(dict.fromkeys(found))


def tag_rasters(name: str, payload: Any, zone: str | None,
                since: float | None = None) -> list[str]:
    """Write the provenance tags into every GeoTIFF `payload` names. Returns
    the paths tagged. A file that cannot be opened for update is skipped,
    never a failure of the tool: the result itself still carries the lines.

    `since` (a POSIX time) limits it to files written at or after it: a result
    may NAME a raster another tool wrote (get_flood_map's permanent-water
    input), and that file keeps its own source and date."""
    import os

    import rasterio

    tags = provenance(name, payload, zone)
    done = []
    for path in raster_paths(payload):
        if since is not None and os.path.getmtime(path) < since - 1.0:
            continue
        try:
            with rasterio.open(path, "r+") as dst:
                dst.update_tags(**tags)
            done.append(path)
        except Exception:                                       # noqa: BLE001
            continue
    return done


def read_tags(path: Any) -> dict[str, str]:
    """The provenance tags of one GeoTIFF ({} if none)."""
    import rasterio

    try:
        with rasterio.open(path) as src:
            tags = src.tags()
    except Exception:                                           # noqa: BLE001
        return {}
    return {k: tags[k] for k in (TAG_SOURCE, TAG_DATED, TAG_UTC, TAG_ZONE, TAG_TOOL)
            if k in tags}


def figure_line(source: str, dated: str) -> str:
    """The one line every figure carries under its title."""
    return (f"Source: {source or 'not recorded'}  |  "
            f"{dated or 'acquisition date and time not recorded'}")


_ZONES: dict[tuple[float, float], str | None] = {}


def zone_at(bbox: Any) -> str | None:
    """The IANA zone at a bbox's centre, looked up once per place per process."""
    try:
        w, s, e, n = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    key = (round((s + n) / 2, 2), round((w + e) / 2, 2))
    if key not in _ZONES:
        from .workflows.availability import zone_lookup

        _ZONES[key] = zone_lookup(*key)[0]
    return _ZONES[key]


def bbox_of(params: Any, payload: Any) -> Any:
    """The AOI a call was for: its parameters first, then the result's own."""
    for d in (params, payload):
        if isinstance(d, dict):
            for path in ("bbox", "aoi.bbox", "meta.bbox"):
                box = _g(d, path)
                if isinstance(box, (list, tuple)) and len(box) == 4:
                    return box
    return None


def figure_line_for(name: str, payload: Any, zone: str | None) -> str:
    """`figure_line` for a figure drawn straight from a result, not a raster."""
    return figure_line(source_line(name, payload), "; ".join(lines(name, payload, zone)))
