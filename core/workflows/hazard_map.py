"""The hazard-and-exposure map of Modules 9 and 10 (user decision 2026-11-02).

One figure per module, on the OpenStreetMap basemap of the flood overlay:

* **Hazards** are layers from `HAZARD_LAYERS`, chosen by the `hazards`
  parameter. Only `flood_extent` exists: the stored flood map, drawn as a red
  dotted hatch (the user's reference image), land the product did not observe
  in grey, and the minor-flood pins of the overlay (BUG-063). A new hazard is
  one more entry -- a builder returning its pixels on the map and a point test
  -- and no change to the drawing or the counts. The registry is open for
  extension on purpose: Modules 9 and 10 will map exposure to more hazards
  than the flood.
* **Exposure** is layers from `EXPOSURE_LAYERS`: hospitals and general
  clinics, drinking-water facilities, and food outlets (supermarkets,
  groceries, other food shops and marketplaces). Hospitals and water are
  markers; food outlets are COUNTED CLUSTERS, semi-transparent amber discs
  with the number of outlets in the middle, never more than
  `CLUSTER_MAX_DIAMETER_KM` (2 km) across. Red is kept for hazards.
* `maps` says which exposure layers go on which figure: Module 9 (essential
  services) shows all three, Module 10 (health) hospitals only.

For every exposure layer and every hazard the located points are tested
against the hazard: inside, outside, or on ground the product did not
observe (never "outside"). Nothing is recomputed from the flood product: the
extent raster is the stored `get_flood_map` result, read as it is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import tool
from . import _fetch as F

# Food clusters are never wider than this (user decision 2026-11-02).
CLUSTER_MAX_DIAMETER_KM = 2.0
# Located points per OSM category. where_am_i keeps 400; a city holds more
# food shops than that, and a cut list would undercount the clusters. The
# cap is still reported when it is reached.
LOCATE_MAX = 5000

FLOOD_DOT_RGBA = (230, 0, 0, 235)      # the dots of the flood hatch
FLOOD_TINT_RGBA = (255, 0, 0, 34)      # a faint wash under them, so the extent reads as an area
DOT_PITCH_PX = 7                       # one 2 x 2 px dot every 7 px

# Exposure markers: never red (red is the hazard).
MARKER_STYLES = {
    "hospitals": ("P", "#0b3d91", "hospital / general clinic"),
    "water_facilities": ("D", "#0091c2", "drinking water facility"),
}
FOOD_STYLE = {"face": "#f5a623", "edge": "#9a5b00", "alpha": 0.38,
              "label": "food shops and markets (count; at most 2 km across)"}


@dataclass(frozen=True)
class ExposureLayer:
    label: str
    categories: tuple[str, ...]     # where_am_i / osm_context category keys
    kind: str                       # "points" or "clusters"


EXPOSURE_LAYERS: dict[str, ExposureLayer] = {
    "hospitals": ExposureLayer("hospitals and general clinics", ("hospitals",), "points"),
    "drinking_water": ExposureLayer("drinking water facilities", ("water_facilities",), "points"),
    "food": ExposureLayer("food shops and markets", ("food_grocery_shops", "marketplaces"),
                          "clusters"),
}

DEFAULT_MAPS = {"essential_services": ["hospitals", "drinking_water", "food"],
                "health": ["hospitals"]}


@dataclass
class HazardContext:
    """What a hazard builder may read."""

    bbox: list[float]
    frame: list[float]
    size: tuple[int, int]
    flood_raster: str
    flood_label: str


@dataclass(frozen=True)
class HazardLayer:
    label: str
    source: str
    # (ctx) -> {"rgba": HxWx4 uint8, "legend": [(rgba, label[, hatch])], "pins": [...], "notes": [...]}
    draw: Callable[[HazardContext], dict[str, Any]]
    # (ctx, lons, lats) -> array of "in" / "out" / "not_observed"
    test: Callable[[HazardContext, np.ndarray, np.ndarray], np.ndarray]


def _flood_draw(ctx: HazardContext) -> dict[str, Any]:
    from .flood_map import FLOOD_RGBA, UNOBSERVED_RGBA, MINOR_FLOOD_LABEL, _minor_floods, shown_codes

    codes = shown_codes(ctx.flood_raster, ctx.bbox, ctx.frame, ctx.size)
    h, w = codes.shape
    rgba = np.zeros((h, w, 4), dtype="uint8")
    flood = codes == 1
    # A solid deep-red fill since 2026-11-19 (user: "the flooded area should
    # be deep red"); it replaced the red dotted hatch, which read too weakly
    # over the light-grey unobserved land.
    rgba[codes == 2] = UNOBSERVED_RGBA
    rgba[flood] = FLOOD_RGBA
    notes: list[str] = []
    try:
        pins = _minor_floods(ctx.flood_raster, ctx.bbox, ctx.frame, ctx.size)
    except Exception as exc:                                   # noqa: BLE001
        pins, notes = [], [f"minor floods not marked: {type(exc).__name__}"]
    return {"rgba": rgba, "pins": pins, "pin_label": MINOR_FLOOD_LABEL, "notes": notes,
            "legend": [(FLOOD_RGBA, f"Flooded ({ctx.flood_label})"),
                       (UNOBSERVED_RGBA, "Not observed in the window (unknown, not dry)")]}


def _flood_test(ctx: HazardContext, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    from .impact import DRY, FLOODED, PERMANENT, _values_at

    vals = _values_at(ctx.flood_raster, lons, lats, 255)
    return np.where(vals == FLOODED, "in",
                    np.where(np.isin(vals, (DRY, PERMANENT)), "out", "not_observed"))


HAZARD_LAYERS: dict[str, HazardLayer] = {
    "flood_extent": HazardLayer("the mapped flood extent",
                                "the stored get_flood_map extent (best-ranked product)",
                                _flood_draw, _flood_test),
}


# -- clusters ------------------------------------------------------------------------
def _metres(lons: np.ndarray, lats: np.ndarray, lat0: float) -> tuple[np.ndarray, np.ndarray]:
    k = 111_320.0
    return lons * k * math.cos(math.radians(lat0)), lats * k


def clusters(points: list[dict[str, Any]],
             max_diameter_km: float = CLUSTER_MAX_DIAMETER_KM) -> list[dict[str, Any]]:
    """Greedy counted clusters, none wider than `max_diameter_km`.

    The densest point (most others within the radius) seeds a cluster that
    takes every unassigned point within the radius of it; repeat. Every member
    is within the radius of the seed, so the disc centred on the seed with
    radius = its farthest member holds them all and is at most the diameter
    across. Deterministic: ties go to the first point in the order given.
    """
    if not points:
        return []
    radius = max_diameter_km * 1000.0 / 2.0
    lons = np.array([p["lon"] for p in points], dtype="float64")
    lats = np.array([p["lat"] for p in points], dtype="float64")
    x, y = _metres(lons, lats, float(lats.mean()))
    d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
    near = d <= radius
    left = np.ones(len(points), dtype=bool)
    score = near.sum(axis=1)
    out = []
    while left.any():
        seed = int(np.argmax(np.where(left, score, -1)))
        members = np.flatnonzero(near[seed] & left)
        left[members] = False
        score = score - near[:, members].sum(axis=1)
        out.append({"lon": round(float(lons[seed]), 6), "lat": round(float(lats[seed]), 6),
                    "radius_m": round(float(d[seed, members].max()), 1),
                    "count": int(len(members)), "members": members.tolist()})
    return out


# -- the tool ------------------------------------------------------------------------
def _located(bbox: list[float]) -> dict[str, Any]:
    from . import osm_context as O

    return O.counts(bbox, locate_max=LOCATE_MAX, groups=("facilities",))


@tool(
    "map_hazard_exposure",
    "The hazard-and-exposure map of Modules 9 and 10: the stored flood extent "
    "(`flood_raster`, from get_flood_map) drawn in deep red on "
    "OpenStreetMap, with hospitals, drinking-water facilities and food outlets "
    "(supermarkets, groceries, markets) as amber counted clusters no wider than "
    "2 km. Counts each exposure layer inside each hazard, outside it, and on "
    "ground not observed. `hazards` names hazard layers (default flood_extent); "
    "`maps` maps a figure name to its exposure layers (default: "
    "essential_services = hospitals, drinking_water, food; health = hospitals).",
    label="Hazard and exposure map",
    source="the stored flood extent + OpenStreetMap",
    order=70,
    data_source_uncertainty=(
        "What: OpenStreetMap holds mapped facilities, not every facility, and a "
        "shop's tag says what it sells only roughly; the flood extent carries its "
        "product's blind spots. Affects: the counts in and out of the flood, and "
        "the cluster sizes. Reduce: compare with a local facility register."),
    method_uncertainty=(
        "What: a facility is tested at one point (its node or the centre of its "
        "outline), so a large site part-flooded may read as dry; clusters are a "
        "greedy grouping within 2 km, not service areas. Affects: which facilities "
        "count as flooded. Reduce: test outlines against the extent."),
)
def map_hazard_exposure(bbox: list[float], flood_raster: str, flood_label: str = "",
                        hazards: list[str] | None = None,
                        maps: dict[str, list[str]] | None = None,
                        output_dir: str = "outputs", event_id: str = "",
                        figure_dir: str = "", aoi_fraction: float = 0.75,
                        write_figure: bool = True) -> dict[str, Any]:
    from .. import dating, places
    from . import osm_context as O

    if len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    bbox = [float(v) for v in bbox]
    if not flood_raster or not Path(flood_raster).is_file():
        raise ValueError(f"flood_raster {flood_raster!r} is not a file: run get_flood_map "
                         "first and pass its raster.path")
    hazards = list(hazards or ["flood_extent"])
    unknown = [h for h in hazards if h not in HAZARD_LAYERS]
    if unknown:
        raise ValueError(f"unknown hazard layer(s) {unknown}; known: {sorted(HAZARD_LAYERS)}")
    maps = {k: list(v) for k, v in (maps or DEFAULT_MAPS).items()}
    bad = sorted({e for v in maps.values() for e in v} - set(EXPOSURE_LAYERS))
    if bad:
        raise ValueError(f"unknown exposure layer(s) {bad}; known: {sorted(EXPOSURE_LAYERS)}")
    label = flood_label or "flood extent"

    located = _located(bbox)
    retrieved = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cats = located.get("facilities") or {}
    frame = O.map_extent(bbox, float(aoi_fraction))
    z = O.choose_zoom(frame)
    folder = Path(figure_dir or output_dir) / (event_id or "hazard_map")
    img, origin = O._stitch(frame, z, folder / "tiles", places.USER_AGENT)
    ctx = HazardContext(bbox, frame, img.size, flood_raster, label)

    # Exposure: located points, tested against every hazard.
    wanted = sorted({e for v in maps.values() for e in v})
    exposure: dict[str, Any] = {}
    food_clusters: list[dict[str, Any]] = []
    for name in wanted:
        layer = EXPOSURE_LAYERS[name]
        items = [dict(i, category=k) for k in layer.categories
                 for i in (cats.get(k) or {}).get("items") or []]
        missing = [k for k in layer.categories if k in (located.get("missing") or [])]
        entry: dict[str, Any] = {
            "label": layer.label, "categories": list(layer.categories),
            "mapped": sum((cats.get(k) or {}).get("count") or 0 for k in layer.categories),
            "located": len(items),
            "cap_reached": any((cats.get(k) or {}).get("truncated") for k in layer.categories),
            "not_located": missing, "in_hazard": {}}
        lons = np.array([i["lon"] for i in items], dtype="float64")
        lats = np.array([i["lat"] for i in items], dtype="float64")
        tests = {h: (HAZARD_LAYERS[h].test(ctx, lons, lats) if items else np.array([], dtype=str))
                 for h in hazards}
        for h, res in tests.items():
            entry["in_hazard"][h] = {
                "in": int((res == "in").sum()), "out": int((res == "out").sum()),
                "not_observed": int((res == "not_observed").sum()),
                "names_in": [i.get("name") or "unnamed" for i, r in zip(items, res) if r == "in"][:25]}
        if layer.kind == "clusters":
            for cl in clusters(items):
                members = cl.pop("members")
                cl["in_hazard"] = {h: {"in": int((tests[h][members] == "in").sum()),
                                       "not_observed": int((tests[h][members] == "not_observed").sum())}
                                   for h in hazards}
                food_clusters.append(cl)
            entry["clusters"] = len(food_clusters)
        else:
            entry["points"] = [{"name": i.get("name") or "", "lon": i["lon"], "lat": i["lat"],
                                "category": i["category"],
                                **{f"in_{h}": str(tests[h][n]) for h in hazards}}
                               for n, i in enumerate(items)]
        exposure[name] = entry

    # Figures: the hazards' pixels composited, then each map's exposure.
    drawn = {h: HAZARD_LAYERS[h].draw(ctx) for h in hazards}
    rgba = np.zeros((img.size[1], img.size[0], 4), dtype="uint8")
    legend, pins, notes = [], [], list(located.get("problem") and [located["problem"]] or [])
    for h in hazards:
        layer = drawn[h]["rgba"]
        on = layer[..., 3] > 0
        rgba[on] = layer[on]
        legend += drawn[h]["legend"]
        pins += drawn[h].get("pins") or []
        notes += drawn[h].get("notes") or []
    for name, entry in exposure.items():
        if entry["cap_reached"]:
            notes.append(f"{entry['label']}: more are mapped than the {LOCATE_MAX:,} located per "
                         "category; the map and counts show the located ones")

    zone = dating.zone_at(bbox)
    out_maps: dict[str, Any] = {}
    if write_figure:
        for fig_name, layers in maps.items():
            markers = [{"category": p["category"], "lon": p["lon"], "lat": p["lat"]}
                       for e in layers if EXPOSURE_LAYERS[e].kind == "points"
                       for p in exposure[e].get("points") or []]
            circles = food_clusters if "food" in layers else []
            # The layers are in the legend; the title stays one short line.
            title = f"{label}: flood and {'health facilities' if layers == ['hospitals'] else 'essential services'}"
            png = folder / f"hazard_exposure_{fig_name}.png"
            O.draw(img, origin, z, bbox, [], title, png, markers=markers,
                   dated=f"OpenStreetMap retrieved {dating.stamp(retrieved, zone)}",
                   overlay=rgba, overlay_legend=legend, overlay_title="Hazard",
                   pins=pins, pin_label=next((drawn[h].get("pin_label") for h in hazards
                                              if drawn[h].get("pin_label")), ""),
                   circles=circles, circle_style=FOOD_STYLE, marker_styles=MARKER_STYLES,
                   markers_title="Exposure (mapped)")
            out_maps[fig_name] = {"png": str(png), "layers": layers}

    tool_items = [F.item("one_point", "Each facility is tested at one point (its node or the "
                         "centre of its outline).", "", "", "assumed")]
    for name, entry in exposure.items():
        if entry["cap_reached"]:
            tool_items.append(F.item(f"{name}_cap", f"{entry['label']}: the located list was cut "
                                     f"at {LOCATE_MAX:,} per category.", LOCATE_MAX, "points",
                                     "measured"))
        if entry["not_located"]:
            tool_items.append(F.item(f"{name}_not_located", f"{entry['label']}: the OpenStreetMap "
                                     f"request failed for {', '.join(entry['not_located'])}; "
                                     "not counted, never 0.", entry["not_located"], "", "measured"))
    first = next(iter(out_maps.values()), None)
    return {
        "status": "ok" if located.get("ok") else "osm_unavailable",
        "bbox": bbox, "flood": {"label": label, "raster": flood_raster},
        "hazards": {h: {"label": HAZARD_LAYERS[h].label, "source": HAZARD_LAYERS[h].source}
                    for h in hazards},
        "exposure": exposure, "food_clusters": food_clusters,
        "cluster_rule": {"max_diameter_km": CLUSTER_MAX_DIAMETER_KM,
                         "method": "greedy: densest point seeds a disc of radius 1 km"},
        "maps": out_maps, "notes": notes, "retrieved_at": retrieved,
        "osm_problem": located.get("problem") or "",
        # One figure for core/figure_refs.register_run; all of them in `maps`.
        **({"figure": {"name": "hazard_exposure", "path": first["png"],
                       "caption": f"{label} flood (deep red; grey = not observed) with exposed facilities"}} if first else {}),
        "uncertainty": F.blocks(
            [F.item("osm_mapped", "OpenStreetMap holds mapped facilities, not every facility.",
                    "", "", "stated_by_source")], tool_items),
        "sources": [F.source("osm-facilities", "OpenStreetMap facilities (Overpass API)",
                             "OpenStreetMap contributors", "https://www.openstreetmap.org",
                             "", "ODbL", retrieved_at=retrieved)],
    }
