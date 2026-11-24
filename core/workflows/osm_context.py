"""where_am_i: what an AOI is, from OpenStreetMap -- before any analysis.

Country-agnostic: nothing here is specific to Sri Lanka. Four sections, each
failing soft on its own (a section that could not be built says so in its
`problem`, and the others still arrive):

1. **Map** -- OpenStreetMap tiles stitched into one raster, framed so the AOI
   fills `aoi_fraction` of the image (0.75 = the box is the middle 75%), with
   the box drawn and the major places and water infrastructure labelled. An
   annotated PNG (the figure) and the plain raster as a GeoTIFF (EPSG:3857).
2. **Admin units and major places** -- the first- and second-order units the
   AOI lies in or crosses (levels read per country, not assumed), and the
   cities and towns inside it with their units.
3. **Counts** -- buildings, hospitals, schools, commercial buildings, food or
   grocery shops, marketplaces and electric substations, counted server-side.
4. **Water-related influences** -- hydropower and dams, water-supply
   reservoirs, river gauges, water treatment and intakes (searched in the AOI
   plus `influence_km`), and river crossings, floodplain industry and quarries,
   and flood protection (searched in the AOI). One request for all seven.

**Everything is what OSM has mapped.** A low count can mean few surveyors; a
category that failed is absent and listed in `missing` -- never reported as 0.
**Nothing is claimed to be upstream**: no river-basin dataset is wired, so the
wide search is by distance, and every item carries distance and bearing.

Tile use follows the OpenStreetMap tile policy: an identifying User-Agent, a
small tile budget per call, tiles cached on disk, and the attribution
"(c) OpenStreetMap contributors" drawn on the figure.
"""

from __future__ import annotations

import io
import math
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import workflow
from .. import cache_policy, dating

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "(c) OpenStreetMap contributors"
MAX_TILES = 64
TARGET_WIDTH_PX = 1400

# key -> (Overpass selector(s), element kinds, the definition a reader is shown).
# Several selectors are a union: a government office is tagged three ways.
COUNTS: dict[str, tuple[str | tuple[str, ...], tuple[str, ...], str]] = {
    "buildings": ('["building"]', ("way", "relation"), "building=* (any)"),
    # The two that fetch_place_context counted and where_am_i did not
    #. Same selectors as core/places.FEATURES, so the two
    # tools count the same thing.
    "houses": ('["building"~"^(house|detached|semidetached_house|bungalow|terrace)$"]',
               ("way", "relation"),
               "building=house|detached|semidetached_house|bungalow|terrace"),
    "apartments": ('["building"~"^(apartments|residential|dormitory)$"]',
                   ("way", "relation"), "building=apartments|residential|dormitory"),
    # Hospitals: an actual hospital or a GENERAL clinic only (user decision
    # 2026-10-27, BUG-038). The selector is wide; KEEP["hospitals"] narrows it
    # on the full tags, and assess_road_access uses both (BUG-037).
    "hospitals": (('["amenity"~"^(hospital|clinic)$"]', '["healthcare"="hospital"]'),
                  ("node", "way", "relation"),
                  "amenity=hospital or healthcare=hospital, and amenity=clinic only when "
                  "general (healthcare:speciality general or emergency, or a general-care "
                  "name: hospital, infirmary, medical or health centre, minor injuries); "
                  "GP surgeries (amenity=doctors) and specialist or alternative clinics "
                  "are not counted"),
    "schools": ('["amenity"="school"]', ("node", "way", "relation"), "amenity=school"),
    "commercial_buildings": ('["building"~"^(commercial|retail|office|industrial|warehouse)$"]',
                             ("way", "relation"),
                             "building=commercial|retail|office|industrial|warehouse"),
    "food_grocery_shops": ('["shop"~"^(supermarket|convenience|grocery|greengrocer|general|'
                           'department_store|food|butcher|seafood|bakery|dairy|deli|farm)$"]',
                           ("node", "way", "relation"),
                           "shop=supermarket|convenience|grocery|greengrocer|general|"
                           "department_store|food|butcher|seafood|bakery|dairy|deli|farm"),
    "marketplaces": ('["amenity"="marketplace"]', ("node", "way", "relation"),
                     "amenity=marketplace"),
    "electric_substations": ('["power"="substation"]', ("node", "way", "relation"),
                             "power=substation"),
    "government": (('["amenity"="townhall"]', '["office"="government"]',
                    '["building"="government"]'),
                   ("node", "way", "relation"),
                   "amenity=townhall | office=government | building=government"),
    "industrial_sites": (('["building"~"^(industrial|factory|warehouse)$"]',
                          '["landuse"="industrial"]'),
                         ("node", "way", "relation"),
                         "building=industrial|factory|warehouse | landuse=industrial"),
    # Drinking water and wastewater apart (BUG-038); a historic, ruined or
    # disused feature is not a facility.
    "water_facilities": (('["man_made"~"^(water_works|water_tower|reservoir_covered)$"]'
                          '[!"historic"][!"ruins"][!"disused"]["building"!="ruins"]',
                          '["man_made"="pumping_station"]["substance"="water"]',
                          '["man_made"="water_well"]["drinking_water"="yes"][!"historic"]'),
                         ("node", "way", "relation"),
                         "drinking water: man_made=water_works|water_tower|reservoir_covered "
                         "(not historic, ruined or disused) | water pumping stations | "
                         "wells marked drinking_water=yes"),
    # Plants and pumping stations; sewer pipes are the network, not a facility.
    "wastewater_facilities": (('["man_made"="wastewater_plant"]',
                               '["man_made"="pumping_station"]["substance"~"^(sewage|wastewater)$"]'),
                              ("node", "way", "relation"),
                              "man_made=wastewater_plant | sewage or wastewater pumping stations"),
}

# Hospital or general clinic? Read on the full tags of each located element.
GENERAL_SPECIALITIES = frozenset({"general", "general_practice", "emergency"})
GENERAL_NAME = re.compile(r"hospital|infirmary|medical (centre|center|group|practice)|"
                          r"health ?(care )?(centre|center|clinic)|minor injur|\bA&E\b", re.I)


def hospital_or_general_clinic(tags: dict[str, Any]) -> bool:
    if tags.get("amenity") == "hospital" or tags.get("healthcare") == "hospital":
        return True
    if tags.get("amenity") != "clinic" or tags.get("healthcare") not in (None, "clinic"):
        return False                  # alternative, vaccination_centre, ...
    specs = {s.strip() for s in (tags.get("healthcare:speciality") or "").split(";") if s.strip()}
    if specs:
        return bool(specs & GENERAL_SPECIALITIES)
    return bool(GENERAL_NAME.search(_name(tags) or ""))


# What a reader is shown for a category key, where the key alone misleads.
LABELS = {"water_facilities": "drinking water facilities",
          "hospitals": "hospitals and general clinics"}


def category_label(key: str) -> str:
    return LABELS.get(key, key.replace("_", " "))


# Categories whose selector is wider than the label: kept per element.
KEEP = {"hospitals": hospital_or_general_clinic}

# The facility categories are also LOCATED (`out center`), for the map's
# buildings-of-interest markers and the point-in-extent checks of the
# essential-services output. Capped per category; the count says how many
# exist, so a cut list is reported as cut, never as the total.
LOCATE_MAX = 400
LOCATED_TAGS = ("amenity", "office", "building", "man_made", "shop", "power",
                "landuse", "substance", "healthcare", "healthcare:speciality", "emergency")


# -- web mercator ----------------------------------------------------------
def _px(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Global pixel coordinates of a point at zoom z (256-px tiles)."""
    n = 256 * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _merc(lon: float, lat: float) -> tuple[float, float]:
    r = 6378137.0
    return math.radians(lon) * r, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * r


def map_extent(bbox: list[float], aoi_fraction: float) -> list[float]:
    """The map frame: the AOI centred, filling `aoi_fraction` of each side."""
    w, s, e, n = bbox
    cx, cy = (w + e) / 2, (s + n) / 2
    hx, hy = (e - w) / 2 / aoi_fraction, (n - s) / 2 / aoi_fraction
    return [cx - hx, cy - hy, cx + hx, cy + hy]


def choose_zoom(extent: list[float]) -> int:
    span = extent[2] - extent[0]
    # The smallest zoom whose image is at least TARGET_WIDTH_PX wide, so labels
    # stay legible; lowered again below if that needs too many tiles.
    z = int(math.ceil(math.log2(TARGET_WIDTH_PX * 360.0 / (256.0 * span))))
    z = max(8, min(16, z))
    while z > 8:
        x0, y0 = _px(extent[0], extent[3], z)
        x1, y1 = _px(extent[2], extent[1], z)
        tiles = (int(x1 // 256) - int(x0 // 256) + 1) * (int(y1 // 256) - int(y0 // 256) + 1)
        if tiles <= MAX_TILES:
            break
        z -= 1
    return z


def _tile(z: int, x: int, y: int, cache: Path, user_agent: str) -> bytes:
    path = cache / str(z) / str(x) / f"{y}.png"
    # The tile cache is a static archive, but an uncached run still bypasses
    # it, so its cost is honest (core/cache_policy.py). It is still written.
    if path.is_file() and not cache_policy.is_off():
        return path.read_bytes()
    req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    time.sleep(0.1)                  # polite pacing between uncached tiles
    return data


def _stitch(extent: list[float], z: int, cache: Path, user_agent: str):
    from PIL import Image

    if cache_policy.is_off():
        from ..logbook import event
        event("cache-state", src="monty", tool="where_am_i",
              note="off: OSM tile cache bypassed (tiles re-fetched)")

    x0, y0 = _px(extent[0], extent[3], z)
    x1, y1 = _px(extent[2], extent[1], z)
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    canvas = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (255, 255, 255))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            img = Image.open(io.BytesIO(_tile(z, tx, ty, cache, user_agent))).convert("RGB")
            canvas.paste(img, ((tx - tx0) * 256, (ty - ty0) * 256))
    left, top = x0 - tx0 * 256, y0 - ty0 * 256
    crop = canvas.crop((round(left), round(top), round(left + x1 - x0), round(top + y1 - y0)))
    return crop, (x0, y0)


# -- Overpass ----------------------------------------------------------------
# -- asking Overpass politely -------------------------------------------------
#
# The Kandy run of 2026-11-17 lost all nine facility counts: 502 in 0.45 s,
# then 429 after 46 s while our own buildings request held the second slot,
# then 502 twice -- three attempts in about 80 s, with 12 s and 24 s pauses
# against a server that was refusing in half a second. So (user, same day):
#
#   * before a retry, the server's own /api/status says when a slot is free,
#     and that is how long we wait (`_slot_wait`);
#   * without that answer, the pause grows (RETRY_PAUSES) with jitter, so two
#     clients that failed together do not retry together;
#   * no single pause exceeds MAX_PAUSE_S and one request waits at most
#     MAX_WAIT_S in total -- a workflow owns its own timeout and nothing above
#     it interrupts one.
OVERPASS_ATTEMPTS = 3
RETRY_PAUSES = (15.0, 45.0, 90.0)      # before attempt 2, 3, (4)
MAX_PAUSE_S = 90.0
MAX_WAIT_S = 180.0


def _slot_wait(url: str) -> float | None:
    """Seconds until Overpass gives this client a slot, from /api/status.

    0 when a slot is free now; None when the status page did not answer or
    said nothing readable -- the caller then backs off on its own schedule.
    Not recorded as a source call: it is a question about the server, not
    data, and must not count as a failed fetch.
    """
    status = url.rsplit("/", 1)[0] + "/status"
    try:
        with urllib.request.urlopen(urllib.request.Request(
                status, headers={"User-Agent": _user_agent()}), timeout=10) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:                                          # noqa: BLE001
        return None
    waits = [int(s) for s in re.findall(r"in (\d+) seconds", text)]
    free = re.search(r"(\d+) slots? available now", text)
    if free and int(free.group(1)) > 0:
        return 0.0
    return float(min(waits)) if waits else None


def _user_agent() -> str:
    from .. import places

    return places.USER_AGENT


def _pause_before(attempt: int, status: int | None, url: str, waited: float) -> float:
    """How long to wait before `attempt` (1-based retries), within the budget.

    A 429 is "no slot for you": the status page's wait is the answer. Any
    other failure (502, 504, a dropped connection) is load, so the pause is
    at least the backoff floor even when a slot is free -- retrying at once
    into an overloaded server is how three attempts went in 80 s.
    """
    import random

    floor = RETRY_PAUSES[min(attempt, len(RETRY_PAUSES)) - 1]
    slot = _slot_wait(url)
    if status == 429 and slot is not None:
        pause = slot + 1.0
    else:
        pause = max(slot or 0.0, floor * random.uniform(0.8, 1.2))
    return max(0.0, min(pause, MAX_PAUSE_S, MAX_WAIT_S - waited))


def _overpass(query: str, timeout: int = 90,
              attempts: int | None = None) -> dict[str, Any]:
    import json
    import urllib.parse

    from .. import places

    from .. import observability

    last = ""
    # Every attempt is reported (BUG found in rq5-batch-1 run 4: a failed
    # facility request left the tool's source failures at 0, so a NOT KNOWN
    # count could not be told from a different answer). A request a later
    # attempt answered is a retry; one no attempt answered is a source failure.
    failed: list[dict[str, Any]] = []
    attempts = attempts or OVERPASS_ATTEMPTS
    waited, status = 0.0, None
    for attempt in range(attempts):
        url = places.OVERPASS_URLS[attempt % len(places.OVERPASS_URLS)]
        if attempt:
            if waited >= MAX_WAIT_S:
                last += f" (gave up after waiting {waited:.0f} s in total)"
                break
            pause = _pause_before(attempt, status, url, waited)
            time.sleep(pause)
            waited += pause
        req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": query}).encode(),
                                     headers={"User-Agent": places.USER_AGENT})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout + 30) as r:
                reply = json.loads(r.read().decode("utf-8", "replace"))
            observability.source_call(url, duration_s=time.perf_counter() - t0)
            observability.settle_attempts(failed, recovered=True)
            return reply
        except Exception as exc:                               # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            status = getattr(exc, "code", None)
            failed.append(observability.source_call(
                url, ok=False, status=status, reason=type(exc).__name__,
                duration_s=time.perf_counter() - t0, record=False))
    observability.settle_attempts(failed, recovered=False)
    raise RuntimeError(f"Overpass did not answer after {len(failed)} attempt"
                       f"{'s' if len(failed) != 1 else ''} ({last})")


def _sections(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Split one Overpass reply at its `make sep` markers into named sections."""
    out: dict[str, list[dict[str, Any]]] = {}
    current = "_head"
    for el in data.get("elements", []):
        if el.get("type") == "sep":
            current = el.get("tags", {}).get("name", "_")
            continue
        out.setdefault(current, []).append(el)
    return out


def _name(tags: dict[str, Any]) -> str | None:
    return tags.get("name:en") or tags.get("name")


def admin_and_places(bbox: list[float]) -> dict[str, Any]:
    """The administrative units the AOI lies in or crosses, and its cities/towns.

    Country-agnostic. Admin levels differ by country (Sri Lanka: province 4,
    district 5; Thailand: province 4, district 6, municipality 7, subdistrict 8;
    checked 2026-10-21), so they are read, not assumed:

    - first-order unit = admin level 4 (3 where a country uses it);
    - second-order unit = the lowest level above that, up to 6.

    A unit is found two ways and unioned: boundaries containing the AOI's
    centre or corners (`is_in`) and boundaries crossing it (`rel(bbox)`); the
    second alone misses an AOI lying wholly inside one unit. One request; its
    sections are split by `make sep` markers.
    """
    w, s, e, n = bbox
    box = f"{s},{w},{n},{e}"
    pts = [((s + n) / 2, (w + e) / 2), (s, w), (s, e), (n, w), (n, e)]
    adm = '["boundary"="administrative"]'
    query = ("[out:json][timeout:120];"
             + "".join(f"is_in({la},{lo})->.p{i};" for i, (la, lo) in enumerate(pts))
             + "(" + "".join(f"rel(pivot.p{i}){adm};" for i in range(len(pts))) + ")->.cont;"
             + f'rel({box}){adm}["admin_level"~"^[3-6]$"]->.cross;'
             + 'make sep name="contains"; out; .cont out tags;'
             + 'make sep name="crosses"; out; .cross out tags;'
             + f'make sep name="places"; out; node({box})["place"~"^(city|town)$"]; out body;'
             + '(.cont; .cross;)->.all; rel.all["admin_level"~"^[3-6]$"]->.u;'
             + 'make sep name="membership"; out;'
             + f'foreach.u->.r(.r out ids; .r map_to_area->.a; node(area.a)["place"~"^(city|town)$"]({box}); out ids;);')
    sec = _sections(_overpass(query))

    units: dict[int, dict[int, str]] = {}          # level -> {relation id: name}
    country = None
    for el in sec.get("contains", []) + sec.get("crosses", []):
        tags = el.get("tags", {})
        try:
            level = int(tags.get("admin_level", ""))
        except ValueError:
            continue
        if level == 2:
            country = _name(tags)
        units.setdefault(level, {})[el["id"]] = _name(tags) or f"relation {el['id']}"
    levels = sorted(units)
    first = next((lv for lv in (4, 3) if lv in units), None)
    second = next((lv for lv in range((first or 3) + 1, 7) if lv in units), None)

    # membership: relation id followed by the ids of the cities/towns inside it
    member_of: dict[int, list[int]] = {}
    current = None
    for el in sec.get("membership", []):
        if el["type"] == "relation":
            current = el["id"]
        elif el["type"] == "node" and current is not None:
            member_of.setdefault(el["id"], []).append(current)

    places = []
    for el in sec.get("places", []):
        tags = el.get("tags", {})
        rels = member_of.get(el["id"], [])
        places.append({
            "name": _name(tags) or "(unnamed)", "type": tags.get("place"),
            "lon": round(el["lon"], 5), "lat": round(el["lat"], 5),
            "first_order": next((units[first][r] for r in rels if first and r in units[first]), None),
            "second_order": next((units[second][r] for r in rels if second and r in units[second]), None),
        })
    places.sort(key=lambda p: (0 if p["type"] == "city" else 1, p["name"]))
    return {
        "ok": True, "country": country, "levels_found": levels,
        "first_order": {"level": first, "units": sorted(units.get(first, {}).values()) if first else []},
        "second_order": {"level": second, "units": sorted(units.get(second, {}).values()) if second else []},
        "major_places": places,
        "note": "Units the AOI lies in or crosses -- not the share of each unit inside it. "
                "Major places are OSM place=city or town inside the AOI.",
        "problem": "" if second else "no second-order unit found between level "
                                     f"{(first or 3) + 1} and 6; only the first order is reported",
    }


# key -> (label, extent: "aoi" | "wide", [Overpass clauses], note)
INFLUENCES: dict[str, tuple[str, str, list[str], str]] = {
    "hydropower_and_dams": (
        "Hydropower plants and dams", "wide",
        ['nwr["waterway"="dam"]', 'nwr["power"="plant"]["plant:source"="hydro"]',
         'nwr["power"="generator"]["generator:source"="hydro"]["name"]'],
        "waterway=dam; power=plant with plant:source=hydro; named hydro generators"),
    "water_supply_reservoirs": (
        "Water supply reservoirs", "wide",
        ['nwr["water"="reservoir"]["name"]', 'nwr["landuse"="reservoir"]["name"]'],
        "named water=reservoir or landuse=reservoir; OSM rarely says what a reservoir "
        "supplies, so supply versus hydropower storage is not separated"),
    "river_gauges": (
        "River gauges", "wide",
        ['nwr["man_made"="monitoring_station"]["monitoring:water_level"="yes"]',
         'nwr["man_made"="monitoring_station"]["name"~"gaug|hydrometr|water level",i]'],
        "man_made=monitoring_station measuring water level; official gauge networks are "
        "often NOT in OSM -- zero here means none mapped, not none present"),
    "water_treatment_and_intakes": (
        "Water treatment and intakes", "wide",
        ['nwr["man_made"="water_works"]', 'nwr["industrial"="water_treatment"]',
         'nwr["man_made"="pumping_station"]["substance"="water"]',
         'nwr["man_made"="wastewater_plant"]'],
        "man_made=water_works, industrial=water_treatment, water pumping stations, "
        "and wastewater plants (kind given per item)"),
    "river_crossings_and_bridges": (
        "River crossings and bridges", "aoi",
        [],                                  # built from geometry: see _crossings()
        "road or rail bridge ways whose line actually crosses a river or canal line in "
        "the AOI (tested on geometry); one bridge is often several ways, so `count` is "
        "ways and `distinct_named` is named bridges"),
    "floodplain_industry_and_quarries": (
        "Floodplain industry and quarries", "aoi",
        ['nwr["landuse"="industrial"]', 'nwr["landuse"="quarry"]', 'nwr["man_made"="works"]'],
        "landuse=industrial or quarry, man_made=works, inside the AOI (in the AOI, not "
        "tested against a floodplain map)"),
    "flood_protection": (
        "Flood protection (embankments, dikes)", "aoi",
        ['nwr["man_made"="dyke"]', 'nwr["man_made"="embankment"]', 'nwr["waterway"="sluice_gate"]',
         'nwr["wall"="flood_wall"]', 'nwr["flood_protection"]'],
        "man_made=dyke, man_made=embankment (any artificial slope), sluice gates, flood "
        "walls, flood_protection=*; zero means none MAPPED"),
}


def _km(lon: float, lat: float, bbox: list[float]) -> float:
    """Distance from a point to the AOI box (0 inside), equirectangular."""
    w, s, e, n = bbox
    dx = max(w - lon, 0.0, lon - e) * 111.320 * math.cos(math.radians(lat))
    dy = max(s - lat, 0.0, lat - n) * 110.574
    return math.hypot(dx, dy)


def _bearing(lon: float, lat: float, bbox: list[float]) -> str:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    ang = math.degrees(math.atan2((lon - cx) * math.cos(math.radians(cy)), lat - cy)) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]


def _segments(geom: list[dict[str, float]]) -> list[tuple[float, float, float, float]]:
    pts = [(g["lon"], g["lat"]) for g in geom or []]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]


def _cross(p: tuple[float, float, float, float], q: tuple[float, float, float, float]) -> bool:
    """Do two segments intersect (touching counts)?"""
    def orient(ax, ay, bx, by, cx, cy):
        v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        return (v > 0) - (v < 0)

    def on(ax, ay, bx, by, cx, cy):
        return min(ax, bx) <= cx <= max(ax, bx) and min(ay, by) <= cy <= max(ay, by)

    x1, y1, x2, y2 = p
    x3, y3, x4, y4 = q
    o1, o2 = orient(x1, y1, x2, y2, x3, y3), orient(x1, y1, x2, y2, x4, y4)
    o3, o4 = orient(x3, y3, x4, y4, x1, y1), orient(x3, y3, x4, y4, x2, y2)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and on(x1, y1, x2, y2, x3, y3)) or (o2 == 0 and on(x1, y1, x2, y2, x4, y4))
            or (o3 == 0 and on(x3, y3, x4, y4, x1, y1)) or (o4 == 0 and on(x3, y3, x4, y4, x2, y2)))


def _crossings(rivers: list[dict[str, Any]], bridges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bridge ways whose geometry crosses a river or canal (grid-indexed)."""
    cell = 0.005
    grid: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for r in rivers:
        for seg in _segments(r.get("geometry")):
            for gx in range(int(min(seg[0], seg[2]) // cell), int(max(seg[0], seg[2]) // cell) + 1):
                for gy in range(int(min(seg[1], seg[3]) // cell), int(max(seg[1], seg[3]) // cell) + 1):
                    grid.setdefault((gx, gy), []).append(seg)
    out = []
    for b in bridges:
        hit = False
        for seg in _segments(b.get("geometry")):
            cands = {c for gx in range(int(min(seg[0], seg[2]) // cell), int(max(seg[0], seg[2]) // cell) + 1)
                     for gy in range(int(min(seg[1], seg[3]) // cell), int(max(seg[1], seg[3]) // cell) + 1)
                     for c in grid.get((gx, gy), [])}
            if any(_cross(seg, c) for c in cands):
                hit = True
                break
        if hit:
            g = b.get("geometry") or []
            b = dict(b)
            b["center"] = {"lon": sum(p["lon"] for p in g) / len(g), "lat": sum(p["lat"] for p in g) / len(g)}
            out.append(b)
    return out


def influences(bbox: list[float], influence_km: float, list_max: int = 15) -> dict[str, Any]:
    """The seven water-related influence categories, in ONE Overpass request.

    "aoi" categories are searched inside the AOI; "wide" ones inside the AOI
    buffered by `influence_km`. **Whether a wide item is upstream is not
    determined** -- no river-basin dataset is wired -- so each item carries its
    distance and bearing from the AOI instead of an upstream claim.
    """
    w, s, e, n = bbox
    lat0 = (s + n) / 2
    dlat, dlon = influence_km / 110.574, influence_km / (111.320 * math.cos(math.radians(lat0)))
    wide = [w - dlon, s - dlat, e + dlon, n + dlat]
    boxes = {"aoi": f"{s},{w},{n},{e}", "wide": f"{wide[1]},{wide[0]},{wide[3]},{wide[2]}"}
    parts = ["[out:json][timeout:180];"]
    for key, (_label, extent, clauses, _note) in INFLUENCES.items():
        b = boxes[extent]
        if key == "river_crossings_and_bridges":
            parts.append(f'make sep name="_rivers"; out; way({b})["waterway"~"^(river|canal)$"]; out geom qt;')
            parts.append(f'make sep name="_bridges"; out; (way({b})["bridge"]["highway"];'
                         f'way({b})["bridge"]["railway"];); out tags geom qt;')
            continue
        parts.append(f'make sep name="{key}"; out;')
        parts.append("(" + "".join(f"{c}({b});" for c in clauses) + "); out tags center qt;")
    sec = _sections(_overpass("".join(parts), timeout=180))
    sec["river_crossings_and_bridges"] = _crossings(sec.get("_rivers", []), sec.get("_bridges", []))

    out: dict[str, Any] = {}
    for key, (label, extent, _clauses, note) in INFLUENCES.items():
        items, named = [], {}
        for el in sec.get(key, []):
            tags = el.get("tags", {})
            c = el.get("center") or {"lon": el.get("lon"), "lat": el.get("lat")}
            if c.get("lon") is None:
                continue
            kind = (tags.get("man_made") or tags.get("waterway") or tags.get("power")
                    or tags.get("landuse") or tags.get("industrial") or tags.get("wall")
                    or tags.get("water") or ("bridge" if tags.get("bridge") else None))
            item = {"name": _name(tags), "kind": kind, "lon": round(c["lon"], 5),
                    "lat": round(c["lat"], 5), "inside_aoi": _km(c["lon"], c["lat"], bbox) == 0.0,
                    "distance_km": round(_km(c["lon"], c["lat"], bbox), 1),
                    "bearing": _bearing(c["lon"], c["lat"], bbox)}
            if tags.get("plant:output:electricity"):
                item["capacity"] = tags["plant:output:electricity"]
            items.append(item)
            if item["name"]:
                named.setdefault((item["name"], kind), item)
        listed = sorted(named.values(), key=lambda i: (not i["inside_aoi"], i["distance_km"], i["name"]))
        out[key] = {"label": label,
                    "searched": "inside the AOI" if extent == "aoi" else f"AOI plus {influence_km:g} km",
                    "count": len(items), "distinct_named": len(named),
                    "inside_aoi": sum(1 for i in items if i["inside_aoi"]),
                    "items": listed[:list_max], "definition": note}
    return {"ok": True, "influence_km": influence_km, "wide_extent": [round(v, 4) for v in wide],
            "categories": out,
            "note": "Searched by distance, not by river basin: an item in the wide search is NOT "
                    "claimed to be upstream. Counts are of features mapped in OSM.",
            "problem": ""}


# The seven counts travel in TWO requests, sent at the same time (Overpass
# allows about two slots per client): the building scans, which are heavy on a
# large AOI, and the facility counts, which are light. One request per category
# (the old way) cost seven queries, their pacing and their retries.
COUNT_GROUPS = {"buildings": ("buildings", "commercial_buildings", "houses", "apartments"),
                "facilities": ("hospitals", "schools", "food_grocery_shops", "marketplaces",
                               "electric_substations", "government", "industrial_sites",
                               "water_facilities", "wastewater_facilities")}
LOCATED = frozenset(COUNT_GROUPS["facilities"])


def _count_query(bbox: list[float], keys: tuple[str, ...],
                 locate_max: int = LOCATE_MAX) -> str:
    w, s, e, n = bbox
    box = f"{s},{w},{n},{e}"
    parts = ["[out:json][timeout:180];"]
    for key in keys:
        selector, kinds, _ = COUNTS[key]
        selectors = (selector,) if isinstance(selector, str) else selector
        parts.append(f'make sep name="{key}"; out;')
        union = "".join(f"{k}({box}){sel};" for sel in selectors for k in kinds)
        # `out` prints the set without consuming it, so one union gives both
        # the total and the located list.
        located = f" out center tags {locate_max};" if key in LOCATED else ""
        parts.append(f"({union}); out count;{located}")
    return "".join(parts)


def _located(elements: list[dict[str, Any]], bbox: list[float] | None = None,
             keep: Any = None) -> list[dict[str, Any]]:
    """Named points of a count. With `bbox`, only those whose point (or way
    centre) lies inside the user's AOI: Overpass returns a way that merely
    crosses the box, and its centre can sit outside it (BUG-018). With
    `keep`, only elements whose full tags it accepts. One entry per OSM id."""
    out, seen = [], set()
    for el in elements:
        if el.get("type") == "count":
            continue
        osm = f"{el.get('type')}/{el.get('id')}"
        if osm in seen or (keep and not keep(el.get("tags") or {})):
            continue
        seen.add(osm)
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        if bbox and not (bbox[0] <= float(lon) <= bbox[2] and bbox[1] <= float(lat) <= bbox[3]):
            continue
        tags = el.get("tags") or {}
        out.append({"name": _name(tags) or "", "lon": round(float(lon), 6),
                    "lat": round(float(lat), 6), "osm": osm,
                    "tags": {k: tags[k] for k in LOCATED_TAGS if k in tags}})
    return out


# -- the per-category answer store --------------------------------------------
#
# Every category Overpass answered is kept on disk, per AOI, with the moment it
# was retrieved, so a later run -- another turn, or the Module 9/10 hazard map
# over the same box -- asks the server only for what is still missing (user,
# 2026-11-17). Only answers are stored: a failure is never written, so a gap
# stays "not measured" until a real answer arrives. Every reused count carries
# its own retrieval time. An uncached run (RQ5) reads nothing from here but
# still writes, like the tile cache.
COUNT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "overpass_counts"
COUNT_CACHE_DAYS = 30
# Starting new count requests stops after this long; what was not asked by
# then is listed as not attempted. A request already sent is not cut short.
COUNT_BUDGET_S = 480.0


def _count_key(bbox: list[float], key: str) -> Path:
    import hashlib
    import json

    selector, kinds, _ = COUNTS[key]
    keep = KEEP.get(key)
    signature = json.dumps([[round(float(v), 5) for v in bbox], key, selector, list(kinds),
                            keep.__name__ if keep else ""])
    return COUNT_CACHE / f"{key}_{hashlib.sha1(signature.encode()).hexdigest()[:16]}.json"


def _tally(elements: list[dict[str, Any]]) -> int | None:
    el = next((e for e in elements if e.get("type") == "count"), None)
    return None if el is None else int((el.get("tags") or {}).get("total", 0))


def _cache_read(bbox: list[float], key: str, locate_max: int) -> dict[str, Any] | None:
    """A stored answer for this category and AOI that serves this request.

    Too old (COUNT_CACHE_DAYS), unreadable, or located with a smaller cap
    than now asked for -- while more exist than it listed -- is no answer.
    """
    import json

    if cache_policy.is_off():
        return None
    try:
        stored = json.loads(_count_key(bbox, key).read_text(encoding="utf-8"))
        when = datetime.fromisoformat(stored["retrieved_at"].replace("Z", "+00:00"))
        elements = list(stored["elements"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (datetime.now(timezone.utc) - when).days > COUNT_CACHE_DAYS:
        return None
    total = _tally(elements)
    if total is None:
        return None
    if key in LOCATED and stored.get("locate_max", 0) < locate_max and total > stored.get("locate_max", 0):
        return None
    if key in LOCATED:
        # The located list as a request with this cap would have returned it.
        listed = [e for e in elements if e.get("type") != "count"][:locate_max]
        elements = [e for e in elements if e.get("type") == "count"] + listed
    return {"elements": elements, "retrieved_at": stored["retrieved_at"]}


def _cache_write(bbox: list[float], key: str, elements: list[dict[str, Any]],
                 locate_max: int, retrieved_at: str) -> None:
    import json

    try:
        path = _count_key(bbox, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key, "bbox": bbox, "locate_max": locate_max,
                                    "retrieved_at": retrieved_at, "elements": elements}),
                        encoding="utf-8")
    except OSError:
        pass                         # a store that cannot be written costs reuse only


def counts(bbox: list[float], locate_max: int = LOCATE_MAX,
           groups: tuple[str, ...] | None = None) -> dict[str, Any]:
    """The feature counts, one request at a time; every category fails soft.

    A category that could not be measured is listed in `missing` -- never
    reported as 0. `groups` limits the requests to some of COUNT_GROUPS (the
    hazard map needs only the facilities); `locate_max` is the cap on located
    points per category.

    Since 2026-11-17 (the Kandy run lost all nine facility counts):

    * **one request at a time, facilities first.** Overpass gives a client
      about two slots, and our own buildings scan holding one is what turned
      the facility request's retry into a 429. Facilities go first because
      Modules 9 and 10 stand on them.
    * **a failed group is retried category by category**, so a refusal costs
      one category, not nine.
    * **answers already stored for this AOI are reused** (`_cache_read`) and
      only the gaps are asked for; `retrieved` dates every count and
      `from_store` names the reused ones.
    """
    groups = tuple(groups or COUNT_GROUPS)
    from .. import places

    area = places.bbox_km2(bbox)
    if area > places.MAX_AOI_KM2:
        return {"ok": False, "counts": {}, "missing": list(COUNTS), "requests": 0,
                "problem": f"AOI is {area:,.0f} km2, over the {places.MAX_AOI_KM2:,} km2 "
                           "ceiling for a feature count; narrow the AOI"}
    results: dict[str, int] = {}
    raw: dict[str, int] = {}
    located: dict[str, list[dict[str, Any]]] = {}
    problems: list[str] = []
    timing: dict[str, float] = {}
    sections: dict[str, list[dict[str, Any]]] = {}
    retrieved: dict[str, str] = {}
    from_store: list[str] = []
    sent = 0
    started = time.monotonic()

    def ask(keys: tuple[str, ...], attempts: int | None = None) -> None:
        """One request for `keys`; keeps every category it answered."""
        nonlocal sent
        sent += 1
        reply = _overpass(_count_query(bbox, keys, locate_max), timeout=180,
                          **({"attempts": attempts} if attempts else {}))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for key, elements in _sections(reply).items():
            if key in keys and _tally(elements) is not None:
                sections[key], retrieved[key] = elements, now
                _cache_write(bbox, key, elements, locate_max, now)

    order = sorted(groups, key=lambda g: (g != "facilities", g))
    for group in order:
        need = []
        for key in COUNT_GROUPS[group]:
            hit = _cache_read(bbox, key, locate_max)
            if hit:
                sections[key], retrieved[key] = hit["elements"], hit["retrieved_at"]
                from_store.append(key)
            else:
                need.append(key)
        if not need:
            continue
        t0 = time.monotonic()
        if t0 - started > COUNT_BUDGET_S:
            problems.append(f"{group} request: not attempted, {COUNT_BUDGET_S:.0f} s budget spent")
            continue
        try:
            ask(tuple(need))
        except Exception as exc:                               # noqa: BLE001
            problems.append(f"{group} request: {type(exc).__name__}: {exc}")
            if len(need) > 1:
                # Smaller queries, one category each: a loaded server answers
                # a small one where it refused the union.
                for key in need:
                    if time.monotonic() - started > COUNT_BUDGET_S:
                        problems.append(f"{key}: not attempted, {COUNT_BUDGET_S:.0f} s "
                                        "budget spent")
                        continue
                    try:
                        ask((key,), attempts=2)
                    except Exception as one:                   # noqa: BLE001
                        problems.append(f"{key}: {type(one).__name__}: {one}")
        timing[group] = round(time.monotonic() - t0, 1)

    for key, elements in sections.items():
        results[key] = _tally(elements) or 0
        if key in LOCATED:
            located[key] = _located(elements, bbox, KEEP.get(key))
            if key in KEEP:
                # The count is what the filter kept; the selector's raw total
                # is kept beside it.
                raw[key] = results[key]
                results[key] = len(located[key])
    facilities = {key: {"count": results[key], "located": len(items),
                        "truncated": raw.get(key, results[key]) > locate_max
                        if key in raw else results[key] > len(items),
                        "items": items, **({"selected_from": raw[key]} if key in raw else {})}
                  for key, items in located.items()}
    return {"ok": bool(results), "counts": results, "facilities": facilities,
            "missing": [k for g in groups for k in COUNT_GROUPS[g] if k not in results],
            # Said, because a reader would otherwise assume either way.
            "clip": ("a feature is counted when any part of it lies inside the AOI "
                     "(Overpass bbox filter); a located point is listed only when "
                     "it lies inside"),
            "requests": sent, "seconds": timing, "locate_max": locate_max,
            # When each count was retrieved (UTC), and which were reused from
            # an earlier answer for this AOI rather than asked again.
            "retrieved": {k: retrieved[k] for k in results},
            "from_store": sorted(k for k in from_store if k in results),
            "problem": "; ".join(problems)}


# -- figure ------------------------------------------------------------------
# Buildings-of-interest markers: category -> (marker, colour, legend label).
MARKERS = {
    "hospitals": ("P", "#d62728", "hospital / general clinic"),
    "schools": ("^", "#9467bd", "school"),
    "water_facilities": ("D", "#1f77b4", "drinking water facility"),
    "wastewater_facilities": ("d", "#17becf", "wastewater facility"),
    "electric_substations": ("X", "#ff7f0e", "substation"),
    "government": ("h", "#2ca02c", "government"),
    "marketplaces": ("v", "#8c564b", "marketplace"),
}


def draw(img, origin: tuple[float, float], z: int, bbox: list[float],
         labels: list[dict[str, Any]], title: str, png: Path,
         markers: list[dict[str, Any]] | None = None, dated: str = "",
         overlay=None, overlay_legend: list[tuple] | None = None,
         overlay_title: str = "", pins: list[dict[str, Any]] | None = None,
         pin_label: str = "", circles: list[dict[str, Any]] | None = None,
         circle_style: dict[str, Any] | None = None,
         marker_styles: dict[str, tuple[str, str, str]] | None = None,
         markers_title: str = "Buildings of interest (mapped)") -> None:
    """The OSM map with the AOI box, places, buildings of interest and, when
    given, an RGBA `overlay` the size of `img` drawn over the tiles and under
    the markers (the flood overlay, BUG-021), with its own legend.

    `circles` are counted clusters ({lon, lat, radius_m, count}) drawn as
    semi-transparent discs with the count in the middle, styled by
    `circle_style` ({face, edge, alpha, label}); `marker_styles` overrides
    MARKERS per category for this figure."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt

    def to_img(lon: float, lat: float) -> tuple[float, float]:
        x, y = _px(lon, lat, z)
        return x - origin[0], y - origin[1]

    wpx, hpx = img.size
    dpi = 100
    fig = plt.figure(figsize=(wpx / dpi, hpx / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(img, extent=(0, wpx, hpx, 0), interpolation="nearest")
    if overlay is not None:
        ax.imshow(overlay, extent=(0, wpx, hpx, 0), interpolation="nearest", zorder=4)
    ax.set_xlim(0, wpx)
    ax.set_ylim(hpx, 0)
    ax.axis("off")
    (x0, y0), (x1, y1) = to_img(bbox[0], bbox[3]), to_img(bbox[2], bbox[1])
    # Red box on the plain map; dark blue under a flood overlay, whose flood
    # is red -- a red box there would read as a flooded edge.
    ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, lw=3.2,
                               ec="#0b3d91" if overlay is not None else "#d62728",
                               ls="-", zorder=5))
    halo = [pe.withStroke(linewidth=3.5, foreground="white")]
    styles = {**MARKERS, **(marker_styles or {})}
    # Clusters under the markers: a disc whose radius is metres on the ground
    # at this zoom and latitude, with its count in the middle.
    import math

    circled = 0
    cs = {"face": "#f5a623", "edge": "#9a5b00", "alpha": 0.38, "label": "cluster", **(circle_style or {})}
    for cl in circles or []:
        x, y = to_img(cl["lon"], cl["lat"])
        if not (0 <= x <= wpx and 0 <= y <= hpx):
            continue
        m_per_px = 156543.03392 * math.cos(math.radians(cl["lat"])) / (2 ** z)
        r = max(cl.get("radius_m", 0) / m_per_px, 9.0)
        ax.add_patch(plt.Circle((x, y), r, facecolor=cs["face"], edgecolor=cs["edge"],
                                alpha=cs["alpha"], lw=1.2, zorder=5.2))
        ax.text(x, y, str(cl["count"]), ha="center", va="center", fontsize=8.5,
                fontweight="bold", color="#3d2600", path_effects=halo, zorder=5.3)
        circled += 1
    shown: set[str] = set()
    for m in markers or []:
        style = styles.get(m["category"])
        x, y = to_img(m["lon"], m["lat"])
        if style is None or not (0 <= x <= wpx and 0 <= y <= hpx):
            continue
        ax.plot(x, y, marker=style[0], ms=7, mfc=style[1], mec="white", mew=0.9,
                ls="none", zorder=5.5)
        shown.add(m["category"])
    if shown or circled:
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch

        handles = [Line2D([], [], marker=styles[c][0], ls="none", mfc=styles[c][1],
                          mec="white", ms=8, label=styles[c][2])
                   for c in styles if c in shown]
        if circled:
            handles.append(Patch(facecolor=cs["face"], edgecolor=cs["edge"], alpha=cs["alpha"],
                                 label=cs["label"]))
        first = ax.legend(handles=handles, loc="lower left", fontsize=9, framealpha=0.85,
                          title=markers_title, title_fontsize=9)
        ax.add_artist(first)
    # Red pins: flood patches too small to see at this scale (BUG-063). The
    # pin's tip sits on the patch.
    pinned = 0
    for pin in pins or []:
        x, y = to_img(pin["lon"], pin["lat"])
        if not (0 <= x <= wpx and 0 <= y <= hpx):
            continue
        ax.plot(x, y - 9, marker="v", ms=13, mfc="#ff0000", mec="white", mew=1.2,
                ls="none", zorder=6.5)
        ax.plot(x, y, marker="o", ms=3, mfc="#ff0000", mec="white", mew=0.6,
                ls="none", zorder=6.6)
        pinned += 1
    if overlay_legend:
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch

        # An entry may carry a third element, a hatch: the dotted flood of
        # the hazard-and-exposure map is shown as dots, not a fill.
        handles = [Patch(facecolor="white" if len(e) > 2 else tuple(c / 255 for c in e[0][:3]),
                         alpha=1.0 if len(e) > 2 else max(0.35, e[0][3] / 255),
                         edgecolor=tuple(c / 255 for c in e[0][:3]) if len(e) > 2 else "0.3",
                         hatch=e[2] if len(e) > 2 else None, label=e[1])
                   for e in overlay_legend]
        if pinned:
            handles.append(Line2D([], [], marker="v", ls="none", mfc="#ff0000", mec="white",
                                  ms=11, label=pin_label or "minor flood"))
        ax.legend(handles=handles,
                  loc="upper right", bbox_to_anchor=(1.0, 1.0 - 44 / hpx),
                  bbox_transform=ax.transAxes, fontsize=9, framealpha=0.9,
                  title=overlay_title or None, title_fontsize=9)
    for lab in labels:
        x, y = to_img(lab["lon"], lab["lat"])
        if not (0 <= x <= wpx and 0 <= y <= hpx):
            continue
        water = lab.get("kind") == "infrastructure"
        ax.plot(x, y, marker="s" if water else "o", ms=7 if water else 5,
                mfc="#1f77b4" if water else "#222222", mec="white", mew=1.2, zorder=6)
        ax.text(x + 7, y - 6, lab["name"], fontsize=11 if lab.get("kind") in ("city", "town") else 9.5,
                fontweight="bold" if lab.get("kind") in ("city", "town") or water else "normal",
                color="#0b3d91" if water else "#111111", path_effects=halo, zorder=7)
    ax.text(10, 22, title, fontsize=13, fontweight="bold", color="#111111", path_effects=halo, zorder=8)
    if overlay is not None:
        # The call-out on the map itself (user, 2026-11-19): a reader who sees
        # only the picture must not read grey land as safe.
        from .flood_map import NOT_OBSERVED_WARNING

        ax.text(10, 48, NOT_OBSERVED_WARNING, fontsize=12, fontweight="bold", color="#7f0000",
                va="top", zorder=8,
                bbox={"facecolor": "#fff3cd", "edgecolor": "#7f0000", "linewidth": 1.4,
                      "boxstyle": "round,pad=0.35"})
    # Source and date in the picture itself (BUG-020): the tiles are the map
    # as OpenStreetMap held it when they were fetched, not at the event.
    import textwrap

    credit = f"{ATTRIBUTION}" + (f"  |  {dated}" if dated else "")
    credit = textwrap.fill(credit, max(40, int(wpx * 0.62 / 5.6)))
    ax.text(wpx - 8, hpx - 8, credit, fontsize=9, ha="right", va="bottom",
            multialignment="right", color="#111111", path_effects=halo, zorder=8)
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=dpi)
    plt.close(fig)


def _geotiff(img, extent: list[float], path: Path) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    west, north = _merc(extent[0], extent[3])
    east, south = _merc(extent[2], extent[1])
    arr = np.asarray(img).transpose(2, 0, 1)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[2], height=arr.shape[1],
                       count=3, dtype="uint8", crs="EPSG:3857", compress="deflate",
                       transform=from_bounds(west, south, east, north, arr.shape[2], arr.shape[1])) as dst:
        dst.write(arr)


@workflow(
    "where_am_i",
    "What an AOI is, from OpenStreetMap, for any country: a map image of the "
    "bounding box (the box drawn, framed to fill aoi_fraction of the image, default "
    "0.75) with major places and water infrastructure labelled; the first- and "
    "second-order administrative units it lies in (e.g. province and district) and "
    "its cities and towns; counts of mapped buildings, hospitals, schools, commercial "
    "buildings, houses, apartments, food or grocery shops, marketplaces, electric "
    "substations, government offices, industrial sites and water facilities (water "
    "works, wastewater plants, wells, towers, water pipelines), the facility ones also "
    "located and drawn on the map; and "
    "water-related influences -- hydropower and dams, water-supply reservoirs, river "
    "gauges, water treatment and intakes (within influence_km, default 60) and river "
    "crossings, floodplain industry and quarries, and flood protection (in the AOI). "
    "Mapped features only; nothing is claimed to be upstream. Use it first, to orient.",
    label="Where am I (OpenStreetMap)",
    source="OpenStreetMap tiles and Overpass API",
    order=5,
    data_source_uncertainty=(
        "Only what OpenStreetMap has mapped; completeness varies by place, and in "
        "Sri Lanka most buildings carry only building=yes."),
    method_uncertainty=(
        "Counts within the bounding box, and nearby features by distance only, so "
        "nothing is claimed to be upstream. A category that failed is listed as "
        "missing, never as 0."),
)
def where_am_i(
    bbox: list[float],
    aoi_fraction: float = 0.75,
    influence_km: float = 60.0,
    output_dir: str = "outputs",
    event_id: str = "",
    write_figure: bool = True,
) -> dict[str, Any]:
    """Map, admin units and major places, feature counts and water influences.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees, anywhere.
        aoi_fraction: share of the map's width and height the AOI box fills
            (0.75 = zoomed out so the box is the middle 75%). 0.2 to 1.0.
        influence_km: how far around the AOI the dams, reservoirs, gauges and
            water works are searched. 0 to 150.
        output_dir: where the map files go, under `<event_id or where_am_i>/`.
        event_id: names the output subdirectory.
        write_figure: false to skip the map.
    """
    from .. import places

    if len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    w, s, e, n = (float(v) for v in bbox)
    if not (w < e and s < n):
        raise ValueError(f"bbox is not west<east, south<north: {bbox}")
    if not 0.2 <= float(aoi_fraction) <= 1.0:
        raise ValueError("aoi_fraction must be between 0.2 and 1.0")
    if not 0.0 <= float(influence_km) <= 150.0:
        raise ValueError("influence_km must be between 0 and 150")
    bbox = [w, s, e, n]
    folder = Path(output_dir) / (event_id or "where_am_i")
    payload: dict[str, Any] = {
        "aoi": {"bbox": bbox, "bbox_area_km2": round(places.bbox_km2(bbox), 2)},
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": ["OpenStreetMap standard tile layer (tile.openstreetmap.org), " + ATTRIBUTION,
                    "OpenStreetMap Overpass API (community survey data)"],
    }

    try:
        payload["admin"] = admin_and_places(bbox)
    except Exception as exc:                                   # noqa: BLE001
        payload["admin"] = {"ok": False, "major_places": [], "problem": f"{type(exc).__name__}: {exc}"}

    result = counts(bbox)
    payload["counts"] = {
        "ok": result.get("ok", False), "counts": result.get("counts", {}),
        "missing": result.get("missing", []), "problem": result.get("problem", ""),
        "requests": result.get("requests"), "seconds": result.get("seconds"),
        "retrieved": result.get("retrieved", {}), "from_store": result.get("from_store", []),
        "definitions": {k: d for k, (_, _, d) in COUNTS.items()},
        "note": "Counts of features mapped in OpenStreetMap, not totals. A missing "
                "category was not measured; it is never shown as 0."
                + (" Reused from an earlier answer for this AOI (see `retrieved` for "
                   "when each was taken): " + ", ".join(result["from_store"]) + "."
                   if result.get("from_store") else ""),
    }
    # Where the buildings of interest are, for the map and the essential-
    # services checks. Located with `out center` (a polygon's centre).
    payload["facilities"] = {
        "ok": bool(result.get("facilities")), "categories": result.get("facilities", {}),
        "max_per_category": LOCATE_MAX,
        "note": "Mapped features only, located at their OSM node or polygon centre. "
                "`truncated` true means more exist than are listed; `count` is the total.",
    }

    try:
        payload["influences"] = influences(bbox, float(influence_km))
    except Exception as exc:                                   # noqa: BLE001
        payload["influences"] = {"ok": False, "categories": {}, "problem": f"{type(exc).__name__}: {exc}"}

    if write_figure:
        try:
            extent = map_extent(bbox, float(aoi_fraction))
            z = choose_zoom(extent)
            img, origin = _stitch(extent, z, folder / "tiles", places.USER_AGENT)
            labels = [{"name": p["name"], "kind": p["type"], "lon": p["lon"], "lat": p["lat"]}
                      for p in payload["admin"].get("major_places", [])]
            infra = ("water_treatment_and_intakes", "hydropower_and_dams", "river_gauges")
            for key in infra:
                for item in (payload["influences"].get("categories", {}).get(key, {}).get("items", [])):
                    if (extent[0] <= item["lon"] <= extent[2] and extent[1] <= item["lat"] <= extent[3]
                            and item.get("kind") != "wastewater_plant"):
                        labels.append({"name": item["name"], "kind": "infrastructure",
                                       "lon": item["lon"], "lat": item["lat"]})
            markers = [{"category": key, "lon": item["lon"], "lat": item["lat"]}
                       for key, cat in payload["facilities"]["categories"].items()
                       for item in cat["items"]]
            png = folder / "where_am_i_map.png"
            draw(img, origin, z, bbox, labels,
                 f"AOI [{w:.4f}, {s:.4f}, {e:.4f}, {n:.4f}]  (box = {aoi_fraction:.0%} of the frame)",
                 png, markers=markers,
                 dated="tiles retrieved " + dating.stamp(payload["retrieved_at"],
                                                         dating.zone_at(bbox)))
            tif = folder / "where_am_i_osm.tif"
            _geotiff(img, extent, tif)
            payload["map"] = {"ok": True, "extent": [round(v, 5) for v in extent], "zoom": z,
                              "size_px": list(img.size), "png": str(png), "raster": str(tif),
                              "raster_crs": "EPSG:3857", "labels": len(labels),
                              "markers": len(markers), "attribution": ATTRIBUTION}
            # `figure` is what core/figure_refs.register_run looks for.
            payload["figure"] = {"name": "where_am_i_map", "path": str(png),
                                 "caption": "OpenStreetMap of the AOI (red box) with its major "
                                            "places, water infrastructure and buildings of "
                                            "interest."}
        except Exception as exc:                               # noqa: BLE001
            payload["map"] = {"ok": False, "problem": f"{type(exc).__name__}: {exc}"}
    return payload
