"""Flood extent and road impact: what the flood reached.

Three tools, each reading what an earlier one produced, never recomputing it:

* `get_flood_extent` -- one product's flood extent over the AOI for the event
  window, from the experiment's validation config (the same readers and the
  same files `accuracy_metrics` uses). The product is the best-ranked one of a
  Flood analyst run (D-24). **A product that maps no flood returns 0 km2 with
  the reason** -- a finding, not a failure.
* `assess_road_access` -- OSM roads crossing that extent, by road class;
  bridges in it; and per hospital whether a flooded road lies within a set
  distance (250 m, an assumption). **With a depth raster** (calc_flood_depth,
  BUG-012) each flooded road sample takes its depth and the Kramer et al.
  (2016) passability class (D-36), with the probability that the class
  changes across the depth error realisations; without one a flooded road is
  "flooded, depth not computed" (D-37).
* `check_slope_tree_roads` -- road segments beside ground that is both steep
  (over 30 degrees, D-26: **source pending**) and tree-covered (WorldCover
  class 10): notes for road accessibility, not a forecast (D-12).

Everything is computed on the product's NATIVE grid, as in the validation
step: no-data -- GFM's
exclusion mask included -- is lost coverage, never dry ground, and areas use
the ground area of a pixel (the AOI's land area over its pixel count), because
Equi7 pixels are not their nominal size.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ..numfmt import share
from . import tool
from . import _fetch as F

FLOODED, DRY, PERMANENT, UNOBSERVED = 1, 0, 2, 255

# D-36 (Kramer et al. 2016), applied when depth exists (D-37, E-3).
PASSABILITY = [("passable by car", 0.0, 0.3), ("emergency vehicles only", 0.3, 0.6),
               ("impassable", 0.6, math.inf)]
DEPTH_NOT_COMPUTED = "flooded, depth not computed"

# Through routes: the links whose loss cuts a network, most important first.
MAIN_ROADS = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
              "secondary", "secondary_link")


def _road_label(road: dict[str, Any]) -> str:
    """'A1 Colombo-Kandy Road', the ref or the name alone, or 'unnamed trunk'."""
    name, ref = (road.get("name") or "").strip(), (road.get("ref") or "").strip()
    if ref and name and name != ref:
        return f"{ref} {name}"
    return name or ref or f"unnamed {road['class'].replace('_link', '')}"


ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
                "residential", "service", "living_street", "motorway_link", "trunk_link",
                "primary_link", "secondary_link", "tertiary_link")
HOSPITAL_DISTANCE_M = 250.0          # assumed (SOURCES.md 8f)
SLOPE_THRESHOLD_DEG = 30.0           # D-26, source pending
SAMPLE_M = 10.0


# -- the flood extent -----------------------------------------------------------
@tool(
    "get_flood_extent",
    "The flood extent of ONE NRT product over the AOI for the event window, "
    "from the experiment's validation config (`config` is its path; `product` "
    "a key in it: gfm, dswx_hls, dswx_s1, mcdwd, vcdwd -- use the best-ranked "
    "product). Union of every pass in the window on the product's native grid; "
    "flood area over land, excluding the sea and permanent water; observed "
    "area and coverage. A product mapping no flood returns 0 km2 with the "
    "reason. Writes the extent raster for the road checks.",
    label="Flood extent",
    source="the chosen NRT product (GFM / OPERA / MODIS / VIIRS)",
    order=70,
    data_source_uncertainty=(
        "The same product blind spots as get_flood_map, for a product named in the "
        "validation config."),
    method_uncertainty=(
        "The same combine-every-pass extent on the product's own grid, with the "
        "window and permanent water taken from the config, so it matches "
        "get_flood_map only when the two configs match."),
)
def get_flood_extent(product: str, config: str, output_dir: str = "outputs",
                     event_id: str = "", write_raster: bool = True,
                     bbox: list[float] | None = None) -> dict[str, Any]:
    from .validation import config as C

    return extent_from_config(C.load(config), product, config_ref=str(config),
                              output_dir=output_dir, event_id=event_id,
                              write_raster=write_raster, bbox=bbox)


def extent_from_config(cfg: dict[str, Any], product: str, *, config_ref: str,
                       output_dir: str = "outputs", event_id: str = "",
                       write_raster: bool = True,
                       bbox: list[float] | None = None) -> dict[str, Any]:
    """The extent computation itself, on a config already in hand.

    Two callers: `get_flood_extent` with an experiment's validation config,
    and `get_flood_map` (BUG-021) with a config it builds from the run -- the
    user's AOI and window, the shared product catalogue, this run's JRC water.
    `config_ref` names where the config came from, for the sources block.
    """
    from .. import places
    from .validation.align import NotEvaluated, composite
    from .validation.domain import _aoi_mask, _permanent_water, _sea

    products = cfg.get("products") or {}
    if product not in products:
        raise ValueError(f"product {product!r} is not in the config; known: {sorted(products)}")
    spec = products[product]
    # The product is read and clipped to the USER'S AOI (user decision
    # 2026-10-26): the config's own bbox is what the validation was set up
    # on, and an extent cut to it would describe a different area whenever
    # the two disagree. A difference is recorded, never silently resolved.
    config_bbox = [float(v) for v in cfg["aoi"]["bbox"]]
    bbox = [float(v) for v in bbox] if bbox else config_bbox
    clip_note = ("" if [round(v, 6) for v in bbox] == [round(v, 6) for v in config_bbox]
                 else f"the config AOI {config_bbox} differs; the product was clipped "
                      f"to the user's AOI {bbox}")
    cfg = {**cfg, "aoi": {**cfg["aoi"], "bbox": bbox}}
    window = list(cfg["temporal"].get("window") or [])
    base = {"product": product, "label": spec.get("label", product), "bbox": bbox,
            "window": window, "sensor": spec.get("sensor", ""),
            "clip": {"to": "user AOI", "config_bbox": config_bbox, "note": clip_note}}
    try:
        comp = composite(cfg, product, spec)
    except NotEvaluated as exc:
        return {**base, "status": "not_known", "flood_km2": None, "reason": str(exc),
                "uncertainty": F.blocks([], [F.item("not_evaluated", f"{product}: {exc}",
                                                    None, "", "measured")]),
                "sources": [F.source(product, spec.get("label", product), "per config",
                                     config_ref, "event window")]}

    grid = {"crs": str(comp["crs"]), "transform": comp["transform"],
            "height": comp["shape"][0], "width": comp["shape"][1],
            "resolution": abs(comp["resolution"][0])}
    arr = comp["array"]
    aoi = _aoi_mask(bbox, grid)
    sea = _sea(bbox, grid, (cfg.get("domain") or {}).get("exclude_wbm_classes") or ["ocean"])
    land = aoi & ~sea
    try:
        permanent, perm_info = _permanent_water(cfg, grid)
    except ValueError as exc:
        permanent, perm_info = np.zeros(arr.shape, dtype=bool), {"problem": str(exc)}
    flooded = land & (arr == FLOODED) & ~permanent
    observed = land & (arr != UNOBSERVED)

    # Ground area of a pixel: the AOI's land area over its land pixel count
    # (rule 7). The bbox area is spherical, then scaled to the land share.
    land_km2 = places.bbox_km2(bbox) * (land.sum() / max(1, aoi.sum()))
    px_km2 = land_km2 / max(1, int(land.sum()))
    flood_km2 = round(int(flooded.sum()) * px_km2, 4)
    observed_km2 = round(int(observed.sum()) * px_km2, 4)
    coverage = round(int(observed.sum()) / max(1, int(land.sum())), 6)
    status = "flood_mapped" if flood_km2 > 0 else "no_flood_mapped"

    codes = np.full(arr.shape, UNOBSERVED, dtype="uint8")
    codes[observed] = DRY
    codes[observed & permanent] = PERMANENT
    codes[flooded] = FLOODED
    raster = None
    if write_raster:
        import rasterio

        folder = Path(output_dir) / (event_id or "flood_extent")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"flood_extent_{product}.tif"
        with rasterio.open(path, "w", driver="GTiff", height=codes.shape[0],
                           width=codes.shape[1], count=1, dtype="uint8", crs=comp["crs"],
                           transform=comp["transform"], nodata=UNOBSERVED,
                           compress="deflate") as dst:
            dst.write(codes, 1)
        raster = {"path": str(path).replace("\\", "/"),
                  "legend": {"0": "dry (observed)", "1": "flooded",
                             "2": "permanent water", "255": "not observed / outside the land"}}

    tool_items = [
        F.item("coverage", f"{share(coverage)} of the land was observed by at least one "
               f"{spec.get('label', product)} pass in the window; the rest is not "
               "known, never dry.", coverage, "fraction of land", "measured"),
        # The data source is named with "passes" (user decision 2026-10-27,
        # BUG-031), so this count reads as the product's own, not Module 1's.
        F.item("observations", f"{comp['observations_with_data']} of "
               f"{len(comp['observations'])} {spec.get('label', product)} passes had valid "
               "pixels over the AOI; "
               "the extent is their union (flood wins over dry).",
               comp["observations_with_data"], "passes", "measured"),
        F.item("pixel_ground_area", f"Areas use {px_km2 * 1e6:.1f} m2 per pixel, the "
               "AOI land area over its pixel count.", round(px_km2 * 1e6, 2), "m2", "measured"),
        F.item("permanent_water", "Permanent water (JRC occurrence >= 90%) is excluded "
               "from the flood area." if "raster" in perm_info else
               f"Permanent water NOT excluded: {perm_info.get('problem')}",
               None, "", "measured"),
    ]
    for skip in comp.get("skipped_items") or []:
        # BUG-022: a pass left out because it lacks its no-data mask, said
        # explicitly -- it is not in the union, so its ground is observed only
        # where another pass saw it.
        tool_items.append(F.item(
            "pass_left_out", f"One {spec.get('label', product)} pass was left out: "
            f"{skip['item']} ({skip.get('datetime') or 'time not given'}) has no "
            f"{', '.join(skip['missing'])} asset, so it cannot be read honestly. Its "
            "ground counts as observed only where another pass saw it.",
            skip["item"], "STAC item", "measured"))
    if status == "no_flood_mapped":
        tool_items.append(F.item("no_flood", f"{spec.get('label', product)} maps no "
                                 "flood over the AOI in the window. Every flood-dependent "
                                 "figure is NOT KNOWN for this product (D-24).", 0, "km2",
                                 "measured"))
    return {**base, "status": status, "flood_km2": flood_km2,
            "observed_km2": observed_km2, "land_km2": round(land_km2, 4),
            "coverage": coverage, "flood_fraction_of_observed":
                round(flood_km2 / observed_km2, 6) if observed_km2 else None,
            "grid": {"crs": grid["crs"], "resolution": grid["resolution"],
                     "shape": list(arr.shape)},
            "observations": comp["observations"], "raster": raster,
            "passes_left_out": comp.get("skipped_items") or [],
            "uncertainty": F.blocks([], tool_items),
            "sources": [F.source(product, spec.get("label", product),
                                 spec.get("provider", "per config"), config_ref,
                                 " to ".join(map(str, window)))]}


# -- roads ------------------------------------------------------------------------
def _roads_and_hospitals(bbox: list[float]) -> tuple[list[dict], list[dict]]:
    """The roads, and the hospitals as where_am_i defines them: the same
    selector, the same filter, the same inside-the-AOI rule (BUG-037)."""
    from .osm_context import COUNTS, KEEP, _located, _overpass, _sections

    w, s, e, n = bbox
    box = f"{s},{w},{n},{e}"
    classes = "|".join(ROAD_CLASSES)
    selectors, kinds, _ = COUNTS["hospitals"]
    union = "".join(f"{k}({box}){sel};" for sel in selectors for k in kinds)
    query = (f'[out:json][timeout:180];way["highway"~"^({classes})$"]({box});out geom tags;'
             f'make sep name="hospitals";out;({union});out center tags;')
    data = _overpass(query, timeout=180)
    sections = _sections(data)
    roads = []
    for el in sections.get("_head", []):
        if el.get("type") == "way" and el.get("geometry"):
            tags = el.get("tags") or {}
            roads.append({"id": el["id"], "class": tags.get("highway", ""),
                          "name": tags.get("name") or tags.get("ref") or "",
                          "ref": tags.get("ref") or "",
                          "bridge": tags.get("bridge", "no") not in ("no", ""),
                          "coords": [(p["lon"], p["lat"]) for p in el["geometry"]]})
    hospitals = [{"name": h["name"], "lon": h["lon"], "lat": h["lat"], "osm": h["osm"]}
                 for h in _located(sections.get("hospitals", []), bbox, KEEP["hospitals"])]
    return roads, hospitals


def _sample(coords: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points every ~SAMPLE_M along a polyline, with the length each carries (m)."""
    lons, lats, lens = [], [], []
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        k = 111_320 * math.cos(math.radians((y0 + y1) / 2))
        seg = math.hypot((x1 - x0) * k, (y1 - y0) * 110_574)
        steps = max(1, int(math.ceil(seg / SAMPLE_M)))
        t = (np.arange(steps) + 0.5) / steps
        lons.append(x0 + (x1 - x0) * t)
        lats.append(y0 + (y1 - y0) * t)
        lens.append(np.full(steps, seg / steps))
    if not lons:
        return np.empty(0), np.empty(0), np.empty(0)
    return np.concatenate(lons), np.concatenate(lats), np.concatenate(lens)


def _values_at(path: str, lons: np.ndarray, lats: np.ndarray, fill: int) -> np.ndarray:
    import rasterio
    from rasterio.warp import transform

    with rasterio.open(path) as src:
        arr = src.read(1)
        xs, ys = transform("EPSG:4326", src.crs, lons.tolist(), lats.tolist())
        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
    rows, cols = np.asarray(rows), np.asarray(cols)
    ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
    out = np.full(lons.shape, fill, dtype=arr.dtype)
    out[ok] = arr[rows[ok], cols[ok]]
    return out


@tool(
    "assess_road_access",
    "Roads and hospital access against a flood extent raster (from "
    "get_flood_extent): km of OSM road by class that is flooded, dry or not "
    "observed; the main roads (motorway to secondary) flooded or not observed, by "
    "name or ref; bridges in the flood extent; and per hospital whether a flooded "
    "road lies within `hospital_distance_m` (default 250 m, an assumption). "
    "With `depth_raster` (from calc_flood_depth) flooded road is classed "
    "passable by car (< 0.3 m), emergency vehicles only (0.3-0.6 m) or "
    "impassable (> 0.6 m), Kramer et al. 2016, with `band_change_raster` the "
    "share whose class is unstable; without it, 'flooded, depth not computed'. "
    "Mapped roads only.",
    label="Road and hospital access",
    source="OpenStreetMap (Overpass) + the flood extent",
    order=71,
    data_source_uncertainty=(
        "Only roads OpenStreetMap has mapped are counted, and errors in the flood "
        "extent and depth carry through."),
    method_uncertainty=(
        "A flooded road near a hospital uses an assumed 250 m distance. The "
        "passability thresholds (0.3 / 0.6 m) are user decision D-36; the article "
        "behind them was not read."),
)
def assess_road_access(bbox: list[float], extent_raster: str,
                       hospital_distance_m: float = HOSPITAL_DISTANCE_M,
                       depth_raster: str = "", band_change_raster: str = "") -> dict[str, Any]:
    if not Path(extent_raster).is_file():
        raise ValueError(f"extent raster not found: {extent_raster}; run get_flood_extent first")
    for name, path in (("depth_raster", depth_raster), ("band_change_raster", band_change_raster)):
        if path and not Path(path).is_file():
            raise ValueError(f"{name} not found: {path}; run calc_flood_depth first")
    passable = {c: 0.0 for c, _, _ in PASSABILITY}
    passable["flooded, no depth"] = 0.0
    unstable_m = 0.0
    bbox = [float(v) for v in bbox]
    roads, hospitals = _roads_and_hospitals(bbox)
    by_class: dict[str, dict[str, float]] = {}
    flooded_pts: list[np.ndarray] = []
    bridges: list[dict[str, Any]] = []
    main: dict[str, dict[str, Any]] = {}
    for road in roads:
        lons, lats, lens = _sample(road["coords"])
        if not lons.size:
            continue
        vals = _values_at(extent_raster, lons, lats, UNOBSERVED)
        inside = (lons >= bbox[0]) & (lons <= bbox[2]) & (lats >= bbox[1]) & (lats <= bbox[3])
        c = by_class.setdefault(road["class"], {"flooded_km": 0.0, "dry_km": 0.0,
                                                "not_observed_km": 0.0})
        c["flooded_km"] += float(lens[inside & (vals == FLOODED)].sum()) / 1000
        c["dry_km"] += float(lens[inside & np.isin(vals, (DRY, PERMANENT))].sum()) / 1000
        c["not_observed_km"] += float(lens[inside & (vals == UNOBSERVED)].sum()) / 1000
        hit = inside & (vals == FLOODED)
        if hit.any() and depth_raster:
            d = _values_at(depth_raster, lons[hit], lats[hit], -9999.0).astype(float)
            lens_hit = lens[hit]
            known = d > -9999.0
            for cls, lo, hi in PASSABILITY:
                passable[cls] += float(lens_hit[known & (d >= lo) & (d < hi)].sum()) / 1000
            passable["flooded, no depth"] += float(lens_hit[~known].sum()) / 1000
            if band_change_raster:
                p = _values_at(band_change_raster, lons[hit], lats[hit], -9999.0).astype(float)
                unstable_m += float(lens_hit[(p > 0.5)].sum())
        if road["class"] in MAIN_ROADS and (hit.any() or (inside & (vals == UNOBSERVED)).any()):
            label = _road_label(road)
            m = main.setdefault(label, {"road": label, "class": road["class"],
                                        "flooded_km": 0.0, "not_observed_km": 0.0})
            m["flooded_km"] += float(lens[hit].sum()) / 1000
            m["not_observed_km"] += float(lens[inside & (vals == UNOBSERVED)].sum()) / 1000
            if MAIN_ROADS.index(road["class"]) < MAIN_ROADS.index(m["class"]):
                m["class"] = road["class"]
        if hit.any():
            flooded_pts.append(np.column_stack([lons[hit], lats[hit]]))
            if road["bridge"]:
                bridges.append({"name": road["name"], "class": road["class"],
                                "osm": f"way/{road['id']}",
                                "flooded_m": round(float(lens[hit].sum()), 1),
                                "status": "in the flood extent (deck height not known)"})
    for c in by_class.values():
        for k in c:
            c[k] = round(c[k], 3)
    pts = np.concatenate(flooded_pts) if flooded_pts else np.empty((0, 2))
    access = []
    for h in hospitals:
        if pts.size:
            k = 111_320 * math.cos(math.radians(h["lat"]))
            d = np.hypot((pts[:, 0] - h["lon"]) * k, (pts[:, 1] - h["lat"]) * 110_574)
            nearest = float(d.min())
        else:
            nearest = None
        own = int(_values_at(extent_raster, np.array([h["lon"]]), np.array([h["lat"]]),
                             UNOBSERVED)[0])
        access.append({**h, "nearest_flooded_road_m": round(nearest, 1) if nearest is not None else None,
                       "access_at_risk": nearest is not None and nearest <= hospital_distance_m,
                       "site": {FLOODED: "flooded", DRY: "dry", PERMANENT: "permanent water"}
                               .get(own, "not observed")})
    total = {k: round(sum(c[k] for c in by_class.values()), 3)
             for k in ("flooded_km", "dry_km", "not_observed_km")}
    inputs = [F.item("osm_roads", "Roads are those mapped in OpenStreetMap; an unmapped "
                     "road is not counted.", len(roads), "ways", "stated_by_source")]
    if depth_raster:
        passable = {k: round(v, 3) for k, v in passable.items()}
        depth_items = [F.item("passability", "Flooded road is classed by the FwDET depth at each "
                              f"{SAMPLE_M:g} m sample: Kramer et al. (2016) 0.3 m for cars, 0.6 m "
                              "for emergency vehicles (D-36). Depth comes from a surface model. "
                              "'Flooded, no depth' is road the product's grid calls flooded and "
                              "the DEM grid does not, after the extent was resampled onto it: "
                              "not known, never filled.", passable, "km", "measured")]
        if band_change_raster:
            depth_items.append(F.item("passability_unstable", f"{unstable_m / 1000:.3f} km of "
                                      "flooded road has a probability above 0.5 of changing "
                                      "class across the depth error realisations.",
                                      round(unstable_m / 1000, 3), "km", "measured"))
    else:
        passable = None
        depth_items = [F.item("depth_not_computed", "Depth is not computed (D-37): flooded roads "
                              "carry no passability class. The Kramer et al. (2016) classes "
                              "apply once a depth raster exists.", None, "", "measured")]
    tool_items = [
        *depth_items,
        F.item("hospital_distance", f"Access is 'at risk' when a flooded road lies within "
               f"{hospital_distance_m:g} m of a hospital -- an assumed distance, not a route.",
               hospital_distance_m, "m", "assumed"),
        F.item("not_observed_roads", f"{total['not_observed_km']} km of road lie on ground "
               "the product did not observe; their state is not known.",
               total["not_observed_km"], "km", "measured"),
        F.item("sampling", f"Roads are sampled every {SAMPLE_M:g} m against the extent's "
               "native pixels.", SAMPLE_M, "m", "measured"),
    ]
    main_rows = sorted(({**m, "flooded_km": round(m["flooded_km"], 3),
                         "not_observed_km": round(m["not_observed_km"], 3)} for m in main.values()),
                       key=lambda m: (-m["flooded_km"], MAIN_ROADS.index(m["class"]), m["road"]))
    return {"bbox": bbox, "extent_raster": extent_raster, "roads_km_by_class": by_class,
            "roads_km_total": total,
            # The through routes (motorway to secondary) the flood reached or
            # the product did not see, by name or ref: the network links a
            # flood cuts (user, 2026-11-22). Unnamed ones are grouped by class.
            "main_roads": main_rows,
            "road_state": "passability by depth (FwDET)" if depth_raster else DEPTH_NOT_COMPUTED,
            "passability_km": passable,
            "passability_unstable_km": round(unstable_m / 1000, 3) if band_change_raster else None,
            "depth_raster": depth_raster or None,
            "bridges_in_flood_extent": bridges,
            "hospitals": access,
            "hospitals_access_at_risk": sum(1 for a in access if a["access_at_risk"]),
            "passability_classes": [{"class": c, "from_m": lo, "to_m": None if hi == math.inf else hi}
                                    for c, lo, hi in PASSABILITY],
            "passability_source": "Kramer et al. (2016), D-36"
                                  + ("" if depth_raster else "; applied when depth exists (D-37)"),
            "uncertainty": F.blocks(inputs, tool_items),
            "sources": [F.source("osm-overpass", "OpenStreetMap via Overpass",
                                 "OpenStreetMap contributors", "https://overpass-api.de/",
                                 "", "ODbL"),
                        F.source("kramer-2016", "Safety criteria for the trafficability of "
                                 "inundated roads in urban floodings", "Kramer, Terheiden & "
                                 "Wieprecht, IJDRR 17", "https://doi.org/10.1016/j.ijdrr.2016.04.003",
                                 "2016", role="supporting")]}


def group_bridges(bridges: list[dict[str, Any]]) -> dict[str, Any]:
    """OSM bridge segments grouped into bridges.

    `assess_road_access` lists the bridge WAYS in the flood extent, and one
    bridge is often several ways (each carriageway, each span). A count of
    ways is not a count of bridges, so this groups the named ones by name and
    keeps the unnamed ones as sections, said as such.
    """
    named: dict[str, float] = {}
    unnamed = 0
    for b in bridges or []:
        name = (b.get("name") or "").strip()
        if name:
            named[name] = named.get(name, 0.0) + float(b.get("flooded_m") or 0)
        else:
            unnamed += 1
    return {"segments": len(bridges or []), "named": sorted(named),
            "named_count": len(named), "unnamed_sections": unnamed}


def facilities_in_extent(where: dict[str, Any], extent_raster: str,
                         keys: tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
    """Point-in-extent for the facilities `where_am_i` located.

    Not a tool: it reads two stored results and counts, for the essential-
    services and health outputs. A facility on ground the product did not
    observe is `not_observed`, never dry.
    """
    categories = (where.get("facilities") or {}).get("categories") or {}
    out: dict[str, dict[str, Any]] = {}
    for key, cat in categories.items():
        if keys is not None and key not in keys:
            continue
        items = cat.get("items") or []
        if not items:
            out[key] = {"flooded": 0, "dry": 0, "not_observed": 0, "flooded_names": [],
                        "located": 0, "count": cat.get("count")}
            continue
        vals = _values_at(extent_raster, np.array([i["lon"] for i in items]),
                          np.array([i["lat"] for i in items]), UNOBSERVED)
        out[key] = {"flooded": int((vals == FLOODED).sum()),
                    "dry": int(np.isin(vals, (DRY, PERMANENT)).sum()),
                    "not_observed": int((vals == UNOBSERVED).sum()),
                    "flooded_names": [i["name"] or "unnamed" for i, v in zip(items, vals)
                                      if v == FLOODED],
                    "located": len(items), "count": cat.get("count"),
                    "truncated": cat.get("truncated", False)}
    return out


# -- slope and trees ------------------------------------------------------------------
@tool(
    "check_slope_tree_roads",
    "Notes for road accessibility, not a forecast: OSM road segments next to "
    "ground that is both steep (slope over `slope_threshold_deg`, default 30 -- "
    "the source of that threshold is pending) on the Copernicus DEM and "
    "tree-covered in WorldCover 2021, where fallen trees or a slip could block "
    "the road. A flat AOI returning few or none is a finding.",
    label="Slope-and-tree road notes",
    source="Copernicus DEM GLO-30 + ESA WorldCover 2021 + OpenStreetMap",
    order=72,
    data_source_uncertainty=(
        "Slope from GLO-30, tree cover from WorldCover 2021 and roads from "
        "OpenStreetMap, each with its own vintage and errors."),
    method_uncertainty=(
        "The 30 degree threshold has no source yet, so the results are notes for "
        "road access, not a forecast of blockage."),
)
def check_slope_tree_roads(bbox: list[float],
                           slope_threshold_deg: float = SLOPE_THRESHOLD_DEG,
                           output_dir: str = "outputs", event_id: str = "") -> dict[str, Any]:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    from .terrain import fetch_terrain_data
    from .worldcover import fetch_worldcover

    bbox = [float(v) for v in bbox]
    terrain = fetch_terrain_data(bbox, output_dir=output_dir, event_id=event_id or "slope_tree",
                                 write_rasters=True)
    wc = fetch_worldcover(bbox, output_dir=output_dir, event_id=event_id or "slope_tree",
                          write_rasters=True)
    # Called here, not through the contract wrapper, and they rewrite the same
    # files fetch_terrain_data wrote earlier in the run: tagged here, under
    # their own names, or they lose their source and date (BUG-020).
    from .. import dating

    zone = dating.zone_at(bbox)
    dating.tag_rasters("terrain", terrain, zone)
    dating.tag_rasters("worldcover", wc, zone)
    slope_path = terrain["slope"]["raster"]["path"]
    with rasterio.open(slope_path) as src:
        slope = src.read(1).astype("float32")
        slope[slope == src.nodata] = np.nan
        transform, crs, shape = src.transform, src.crs, slope.shape
    # Tree share of each 30 m DEM cell: WorldCover's 10 m tree class averaged
    # onto the DEM grid (the ratio is not an integer, so this is a resampling,
    # stated in the uncertainty block). A cell is tree-covered at >= 50%.
    with rasterio.open(wc["classes"]["raster"]["path"]) as src:
        tree10 = (src.read(1) == 10).astype("float32")
        tree = np.zeros(shape, dtype="float32")
        reproject(tree10, tree, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs=crs, resampling=Resampling.average)
    hazard = (slope > slope_threshold_deg) & (tree >= 0.5)
    # "Next to": the cell itself or one of its eight neighbours (~30 m).
    near = hazard.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            near |= np.roll(np.roll(hazard, dy, 0), dx, 1)

    roads, _ = _roads_and_hospitals(bbox)
    notes = []
    for road in roads:
        lons, lats, lens = _sample(road["coords"])
        if not lons.size:
            continue
        rows, cols = rasterio.transform.rowcol(transform, lons.tolist(), lats.tolist())
        rows, cols = np.asarray(rows), np.asarray(cols)
        # On the grid AND inside the user's AOI: the terrain grid carries a
        # one-pixel ring outside it for Horn's kernel (BUG-018).
        ok = ((rows >= 0) & (rows < shape[0]) & (cols >= 0) & (cols < shape[1])
              & (lons >= bbox[0]) & (lons <= bbox[2]) & (lats >= bbox[1]) & (lats <= bbox[3]))
        flag = np.zeros(lons.shape, dtype=bool)
        flag[ok] = near[rows[ok], cols[ok]]
        if flag.any():
            i = np.flatnonzero(flag)
            notes.append({"road": road["name"] or "(unnamed)", "class": road["class"],
                          "osm": f"way/{road['id']}", "length_m": round(float(lens[flag].sum()), 1),
                          "lon": round(float(lons[i].mean()), 6), "lat": round(float(lats[i].mean()), 6),
                          # The slope UNDER the road; the steep cell that flagged it
                          # is a neighbour and is steeper than the threshold.
                          "slope_at_road_deg_max": round(float(np.nanmax(
                              slope[rows[i[ok[i]]], cols[i[ok[i]]]])), 1) if ok[i].any() else None,
                          "note": "steep, tree-covered ground beside the road: fallen trees "
                                  "or a slip could block it"})
    notes.sort(key=lambda n: -n["length_m"])
    steep_share = float(np.nanmean(slope > slope_threshold_deg))
    inputs = [F.item("slope_threshold", f"Slope over {slope_threshold_deg:g} degrees is "
                     "treated as risky (D-26); the source of this threshold is pending.",
                     slope_threshold_deg, "deg", "assumed"),
              F.item("dem_surface", "GLO-30 is a surface model: canopy height inflates "
                     "slope under trees, which is exactly where this check looks.", None, "",
                     "stated_by_source"),
              F.item("worldcover_snapshot", "Tree cover is WorldCover 2021, not the "
                     "event year.", 2021, "year", "stated_by_source", "2021")]
    tool_items = [F.item("steep_share", f"{steep_share:.4f} of the DEM cells exceed the "
                         "threshold.", round(steep_share, 6), "fraction", "measured"),
                  F.item("tree_resampling", "WorldCover's 10 m tree class is averaged onto "
                         "the 30 m DEM grid; a cell counts as tree-covered at >= 50%.",
                         0.5, "fraction", "assumed")]
    return {"bbox": bbox, "slope_threshold_deg": slope_threshold_deg,
            "threshold_source": "pending (D-26)", "notes": notes, "count": len(notes),
            "hazard_cells": int(hazard.sum()), "steep_cell_share": round(steep_share, 6),
            "finding": ("no road lies beside steep, tree-covered ground"
                        if not notes else f"{len(notes)} road segment(s) flagged"),
            "uncertainty": F.blocks(inputs, tool_items),
            "sources": [F.source("glo30", "Copernicus DEM GLO-30", "ESA & Airbus",
                                 "doi:10.5270/ESA-c5d3d65", "2011-2015"),
                        *wc["sources"],
                        F.source("osm-overpass", "OpenStreetMap via Overpass",
                                 "OpenStreetMap contributors", "https://overpass-api.de/",
                                 "", "ODbL", "supporting")]}
