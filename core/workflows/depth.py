"""Floodwater depth from a flood extent and the GLO-30 DEM: FwDET v2.1 (E-3, BUG-012).

**The method is FwDET v2.1** (Cohen et al. 2022, Remote Sensing 14, 5313,
section 2.1; docs/SOURCES.md 8g), on the 30 m Copernicus DEM (D-17) and the
best-ranked product's extent (D-24):

1. boundary cells: the cells at the edge of the flooded domain, with their
   DEM elevation;
2. the v2.0 coastal filter: boundary cells that have, or are next to, an
   elevation at or below 0 m are removed;
3. the v2.1 slope filter: boundary cells whose topographic slope is BELOW a
   threshold (percent) are removed -- "true flood boundaries ... are less
   likely to have a very low topographic slope";
4. the v2.1 smoothing: a 5 x 5 focal mean of the boundary elevation, repeated
   `smoothing_iterations` times;
5. each flooded cell takes the (smoothed) elevation of its NEAREST boundary
   cell as its local water surface;
6. depth = water surface - DEM.

Defaults are the paper's stated optimum "for most DEMs": ten iterations and a
0.5 % slope threshold. The paper did not test GLO-30; for the 30 m NASADEM its
own optimum was [10, 2.5 %] (Table 3). Both are parameters and both are
written into the payload.

**Where this implementation fills what the paper leaves open** (each one is a
`basis: assumed` item in the uncertainty block, never silent):

* boundary cells are the OBSERVED DRY cells 8-adjacent to a flooded cell. A
  cell the product did not observe, or permanent water, is no shoreline: its
  elevation is not the water's edge. (The reference GEE script treats any
  masked cell as non-flood; this repo keeps no-data as not known.)
* the smoothed raster is masked back to the boundary cells after each pass,
  so the smoothing stays a smoothing OF the boundary;
* nearest is Euclidean on the ground (metres), the DEM being geographic;
* a negative depth is set to 0, and its share is reported;
* the extent is put onto the DEM grid by nearest neighbour -- a categorical
  mask, so no class is invented (the landmask.py argument). Depth is computed
  on the DEM grid, as FwDET prescribes ("the same grid-cell size and
  alignment as the input DEM").

The reference GEE script (csdms-contrib/fwdet, FwDET2p1_GEE.txt) differs from
the paper in three places, and the paper is what is cited: its slope test
KEEPS cells below the threshold, it has no coastal filter, and it convolves
the depth once more at the end. None of the three is done here.

**The depth error** is its own
flow: K seeded DEM perturbations within GLO-30's published RELATIVE vertical
accuracy (Product Handbook v5.0, 2022: < 2 m at slope <= 20 %, < 4 m above,
90 % point-to-point), each with the boundary as mapped, dilated one pixel and
eroded one pixel. The relative figure, not the absolute one, because a
constant offset of the DEM moves the water surface and the ground together
and cancels out of every depth. The result is a precision statement, never
an accuracy one: no gauge or survey depth exists for the AOI.

**Depth on GLO-30 is reported as BIASED HIGH** (user decision 2026-10-26,
BUG-014). The DEM is a surface model, so trees and roofs on the dry edge lift
the water surface FwDET reads. Every payload carries `bias` and every report
says so beside each depth figure, with the measured edge-above-ground
diagnostic. That diagnostic shows the DIRECTION, not the size: on bare earth
the edge would also stand above the flooded ground, by about the water depth,
so how much of it is canopy or roof is not measured, and is never stated.

**Bands.** The vehicle classes are D-36 (Kramer et al. 2016: 0.3 m cars, 0.6 m
emergency vehicles); the 10 cm advisory is D-16. The person bands of D-25
need the average adult height and the body-segment proportions, and neither
figure has been read from its source (SOURCES.md 8g): they are reported as
NOT KNOWN until `report_config.PERSON_DEPTH_BANDS` is filled from a source.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from . import tool
from . import _fetch as F
from . import _raster as R

FLOODED, DRY, PERMANENT, UNOBSERVED = 1, 0, 2, 255

# Copernicus DEM Product Handbook v5.0 (2022), section 1.2 / 2.2: relative
# vertical accuracy, 90 % linear point-to-point error within 1 x 1 degree.
REL_LE90_M = ((20.0, 2.0), (math.inf, 4.0))      # (slope % upper bound, LE90 m)
ABS_LE90_M = 4.0                                  # section 1.2 / 2.1, < 4 m
Z90 = 1.6448536269514722                          # LE90 = 1.645 sigma if normal

VEHICLE = [("passable by car", 0.0, 0.3), ("emergency vehicles only", 0.3, 0.6),
           ("impassable", 0.6, math.inf)]         # D-36, Kramer et al. 2016
ADVISORY_M = 0.10                                 # D-16
BIAS_LABEL = "biased high"                        # BUG-014, user decision 2026-10-26
BIAS_REASON = ("the DEM (GLO-30) is a surface model: trees and buildings on the flood's "
               "dry edge lift the water surface, so depth reads high; by how much is not "
               "measured")
BOUNDARY_SHIFTS = ("as mapped", "dilated 1 px", "eroded 1 px")


# -- the FwDET core ----------------------------------------------------------------
def _ring(mask: np.ndarray) -> np.ndarray:
    from scipy.ndimage import binary_dilation

    return binary_dilation(mask, structure=np.ones((3, 3), bool))


def slope_percent(dem: np.ndarray, latitude: float, resolution_deg: float,
                  resolution_y_deg: float | None = None) -> np.ndarray:
    from .terrain import slope_degrees

    return np.tan(np.radians(slope_degrees(dem, latitude, resolution_deg,
                                           resolution_y_deg))) * 100.0


def _focal_mean(values: np.ndarray, size: int = 5) -> np.ndarray:
    """Mean over a size x size window, ignoring NaN (ArcGIS Focal Statistics)."""
    from scipy.ndimage import uniform_filter

    have = np.isfinite(values)
    total = uniform_filter(np.where(have, values, 0.0), size=size, mode="constant")
    count = uniform_filter(have.astype(float), size=size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def fwdet(dem: np.ndarray, codes: np.ndarray, *, latitude: float, resolution_deg: float,
          smoothing_iterations: int = 10, slope_threshold_pct: float = 0.5,
          slope_pct: np.ndarray | None = None,
          resolution_y_deg: float | None = None) -> dict[str, Any]:
    """One FwDET v2.1 run on one grid. `codes`: 0 dry, 1 flooded, 2 permanent
    water, 255 not observed. Returns the depth (NaN off the flood) and counts."""
    from scipy.ndimage import distance_transform_edt

    valid = np.isfinite(dem)
    flooded = (codes == FLOODED) & valid
    boundary = (codes == DRY) & valid & _ring(flooded)
    initial = int(boundary.sum())
    low = _ring(valid & (dem <= 0.0))
    removed_coastal = int((boundary & low).sum())
    boundary &= ~low
    if slope_threshold_pct > 0:
        if slope_pct is None:
            slope_pct = slope_percent(dem, latitude, resolution_deg, resolution_y_deg)
        # A slope that cannot be computed (the grid's outer ring) is no
        # evidence of flat ground: only a measured low slope removes a cell.
        flat = slope_pct < slope_threshold_pct
        removed_slope = int((boundary & flat).sum())
        boundary &= ~flat
    else:
        removed_slope = 0
    counts = {"cells_initial": initial, "removed_coastal": removed_coastal,
              "removed_slope": removed_slope, "cells_used": int(boundary.sum()),
              "flooded_cells": int(flooded.sum())}
    # The surface-model signal: how far the dry edge stands above the flooded
    # ground. On a bare-earth DEM a flood edge sits near the water; trees and
    # roofs on the edge lift it, and every depth inherits the lift.
    if boundary.any() and flooded.any():
        counts["edge_above_flood_ground_m"] = round(float(np.median(dem[boundary])
                                                          - np.median(dem[flooded])), 3)
    if not boundary.any() or not flooded.any():
        return {"depth": None, "counts": counts, "negative_share": None}

    edge = np.where(boundary, dem, np.nan)
    for _ in range(int(smoothing_iterations)):
        edge = np.where(boundary, _focal_mean(edge, 5), np.nan)

    dy = (resolution_deg if resolution_y_deg is None else resolution_y_deg) * 110_574.0
    dx = resolution_deg * 111_320.0 * math.cos(math.radians(latitude))
    _, (ri, ci) = distance_transform_edt(~boundary, sampling=(dy, dx), return_indices=True)
    surface = edge[ri, ci]
    raw = surface - dem
    depth = np.where(flooded, np.maximum(raw, 0.0), np.nan)
    negative = int((flooded & (raw < 0)).sum())
    return {"depth": depth.astype("float32"), "counts": counts,
            "negative_share": negative / max(1, counts["flooded_cells"])}


# -- the error method ------------------------------------------------------------------
def dem_sigma(slope_pct: np.ndarray) -> np.ndarray:
    """Per-pixel standard deviation from the relative LE90, assuming normal errors."""
    le90 = np.where(slope_pct > REL_LE90_M[0][0], REL_LE90_M[1][1], REL_LE90_M[0][1])
    return le90 / Z90


def _shift(codes: np.ndarray, how: str) -> np.ndarray:
    """The extent as mapped, or with its flooded area grown or shrunk by one
    pixel ALONG ITS EDGE WITH OBSERVED DRY LAND only. The flood's edge against
    unobserved ground, permanent water or the grid's own border is not a
    mapped boundary, and moving it would be inventing one."""
    out = codes.copy()
    flooded = codes == FLOODED
    dry = codes == DRY
    if how == "dilated 1 px":
        out[dry & _ring(flooded)] = FLOODED
    elif how == "eroded 1 px":
        out[flooded & _ring(dry)] = DRY
    return out


def band_of(depth: np.ndarray, edges: list[float]) -> np.ndarray:
    """Band index per pixel for ascending band edges (0 below edges[0] ...)."""
    return np.digitize(depth, edges)


def error_realisations(dem: np.ndarray, codes: np.ndarray, *, latitude: float,
                       resolution_deg: float, realisations: int, seed: int,
                       smoothing_iterations: int, slope_threshold_pct: float,
                       correlation_px: float = 0.0,
                       resolution_y_deg: float | None = None) -> dict[str, Any]:
    """3K FwDET runs: K DEM perturbations x the three boundaries. Depth on the
    mapped flood; a pixel the eroded boundary leaves dry counts as 0 m."""
    from scipy.ndimage import gaussian_filter

    slope = slope_percent(dem, latitude, resolution_deg, resolution_y_deg)
    sigma = dem_sigma(np.nan_to_num(slope, nan=0.0))
    mapped = codes == FLOODED
    rng = np.random.default_rng(seed)
    stack = []
    for _ in range(int(realisations)):
        noise = rng.standard_normal(dem.shape)
        if correlation_px > 0:
            noise = gaussian_filter(noise, correlation_px)
            noise /= max(float(noise.std()), 1e-12)
        dem_k = dem + noise * sigma
        # The slope filter keeps the UNPERTURBED slope: pixel noise of about a
        # metre at 30 m spacing would make every cell "steep" and switch the
        # filter off in every realisation -- an artefact of the error model,
        # not of the terrain (an assumed choice, said in the payload).
        for how in BOUNDARY_SHIFTS:
            run = fwdet(dem_k, _shift(codes, how), latitude=latitude, resolution_deg=resolution_deg,
                        smoothing_iterations=smoothing_iterations,
                        slope_threshold_pct=slope_threshold_pct, slope_pct=slope,
                        resolution_y_deg=resolution_y_deg)
            d = run["depth"] if run["depth"] is not None else np.full(dem.shape, np.nan, "float32")
            stack.append(np.where(mapped, np.nan_to_num(d, nan=0.0), np.nan).astype("float32"))
    cube = np.stack(stack)
    # Statistics over the mapped flood only; everything else stays NaN.
    flat = cube[:, mapped]
    out = {}
    for name, values in (("median", np.median(flat, axis=0)),
                         ("p05", np.percentile(flat, 5, axis=0)),
                         ("p95", np.percentile(flat, 95, axis=0)),
                         ("sd", np.std(flat, axis=0))):
        grid = np.full(dem.shape, np.nan, dtype="float32")
        grid[mapped] = values
        out[name] = grid
    return {"cube": cube, **out}


def change_probability(estimate: np.ndarray, cube: np.ndarray, edges: list[float]) -> np.ndarray:
    """Per pixel: the share of realisations whose band differs from the estimate's."""
    est = band_of(np.nan_to_num(estimate, nan=0.0), edges)
    bands = band_of(np.nan_to_num(cube, nan=0.0), edges)
    prob = (bands != est[None, :, :]).mean(axis=0)
    return np.where(np.isfinite(estimate), prob, np.nan)


# -- the tool -------------------------------------------------------------------------
def _read_dem(bbox: list[float]):
    from .terrain import tile_url

    urls = R.tiles_for_bbox(bbox, tile_url, step=1.0)
    if len(urls) > 1:
        plan = R.plan_mosaic(urls, bbox, pad=1)
        dem = R.read_band_mosaic(urls, plan)
    else:
        plan = R.plan_grid(urls[0], bbox, pad=1)
        dem = R.read_band(urls[0], plan)
    return dem, plan, urls


def _extent_on(plan, extent_raster: str) -> tuple[np.ndarray, dict[str, Any]]:
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(extent_raster) as src:
        codes = src.read(1)
        src_crs, src_transform = src.crs, src.transform
        src_res = abs(src.transform.a)
    out = np.full(plan.shape, UNOBSERVED, dtype="uint8")
    reproject(source=codes, destination=out, src_transform=src_transform, src_crs=src_crs,
              dst_transform=plan.transform, dst_crs=plan.crs, resampling=Resampling.nearest,
              src_nodata=UNOBSERVED, dst_nodata=UNOBSERVED)
    return out, {"source_crs": str(src_crs), "source_resolution": src_res,
                 "resampling": "nearest neighbour (categorical)"}


def _classes(depth: np.ndarray, prob: np.ndarray, px_km2: float) -> list[dict[str, Any]]:
    flooded = np.isfinite(depth)
    total = max(1, int(flooded.sum()))
    rows = []
    for name, lo, hi in VEHICLE:
        m = flooded & (depth >= lo) & (depth < hi)
        n = int(m.sum())
        stable = int((m & (prob <= 0.5)).sum())
        rows.append({"class": name, "from_m": lo, "to_m": None if hi == math.inf else hi,
                     "km2": round(n * px_km2, 4), "share": round(n / total, 4),
                     "stable_share": round(stable / n, 4) if n else None})
    return rows


@tool(
    "calc_flood_depth",
    "Floodwater depth from a flood extent raster (from get_flood_map or get_flood_extent: use the "
    "best-ranked product's) and the 30 m Copernicus DEM GLO-30, by FwDET v2.1 "
    "(Cohen et al. 2022): boundary elevations, coastal and slope filters, 5x5 "
    "smoothing, nearest-boundary water surface minus the DEM. Also the depth "
    "error: K seeded DEM perturbations within GLO-30's relative vertical accuracy, "
    "each with the boundary as mapped and shifted one pixel, giving the per-pixel "
    "spread and the probability a pixel changes depth band. Vehicle classes "
    "0.3 / 0.6 m (D-36) and the 10 cm advisory. Writes the depth rasters.",
    label="Flood depth",
    source="FwDET v2.1 on Copernicus DEM GLO-30",
    order=72,
    data_source_uncertainty=(
        "What: GLO-30 is a surface model, so building and canopy heights raise the "
        "edge elevations and depth is biased high (BUG-014); any error in the flood "
        "extent passes straight into depth. Affects: depth bands, road passability "
        "and the people-in-depth bands. Reduce: a bare-earth DEM (FABDEM, for "
        "example) or a check against gauge water levels."),
    method_uncertainty=(
        "What: the paper's defaults (10 smoothing passes, 0.5 % slope threshold) "
        "were never tested on GLO-30; the water surface comes from the nearest "
        "flood edge and negative depths are set to 0; the error estimate is 20 "
        "seeded DEM perturbations assuming normal, independent pixel errors. "
        "Affects: the spread of depths and the chance a pixel changes band. Reduce: "
        "tune the parameters on a known event, and add spatially correlated errors."),
)
def calc_flood_depth(bbox: list[float], extent_raster: str, output_dir: str = "outputs",
                     event_id: str = "", smoothing_iterations: int = 10,
                     slope_threshold_pct: float = 0.5, realisations: int = 20,
                     seed: int = 20251128, error_correlation_px: float = 0.0,
                     write_rasters: bool = True) -> dict[str, Any]:
    from .. import report_config

    if not Path(extent_raster).is_file():
        raise ValueError(f"extent raster not found: {extent_raster}; run get_flood_map first")
    if realisations < 1:
        raise ValueError("realisations must be at least 1")
    bbox = [float(v) for v in bbox]
    lat = (bbox[1] + bbox[3]) / 2
    dem, plan, urls = _read_dem(bbox)
    codes, resampled = _extent_on(plan, extent_raster)
    res, res_y = float(plan.resolution), float(plan.resolution_y)
    px_km2 = float(plan.pixel_area_km2)
    product = Path(extent_raster).stem.replace("flood_extent_", "")

    run = fwdet(dem, codes, latitude=lat, resolution_deg=res, resolution_y_deg=res_y,
                smoothing_iterations=smoothing_iterations, slope_threshold_pct=slope_threshold_pct)
    counts = run["counts"]
    if run["depth"] is None:
        why = ("no flooded cell on the DEM grid" if not counts["flooded_cells"] else
               "no boundary cell survived the filters")
        raise ValueError(f"depth cannot be computed: {why} ({counts})")
    depth = run["depth"]
    err = error_realisations(dem, codes, latitude=lat, resolution_deg=res, resolution_y_deg=res_y,
                             realisations=realisations, seed=seed,
                             smoothing_iterations=smoothing_iterations,
                             slope_threshold_pct=slope_threshold_pct,
                             correlation_px=error_correlation_px)
    vehicle_edges = [hi for _, _, hi in VEHICLE[:-1]]
    prob_vehicle = change_probability(depth, err["cube"], vehicle_edges)
    prob_advisory = change_probability(depth, err["cube"], [ADVISORY_M])
    flooded = np.isfinite(depth)
    nf = max(1, int(flooded.sum()))
    unstable_vehicle = float((flooded & (prob_vehicle > 0.5)).sum()) / nf
    unstable_advisory = float((flooded & (prob_advisory > 0.5)).sum()) / nf
    at_or_above = flooded & (depth >= ADVISORY_M)

    rasters: dict[str, Any] = {}
    if write_rasters:
        folder = Path(output_dir) / (event_id or "flood_depth")
        for name, arr in (("depth", depth), ("depth_median", err["median"]),
                          ("depth_p05", err["p05"]), ("depth_p95", err["p95"]),
                          ("depth_sd", err["sd"]), ("band_change_prob_vehicle", prob_vehicle),
                          ("band_change_prob_advisory", prob_advisory)):
            rasters[name] = R.write_float(str(folder / f"{name}_{product}.tif"), arr, plan)

    person = report_config.PERSON_DEPTH_BANDS
    method = {
        "name": "FwDET v2.1 (Cohen et al. 2022)", "smoothing_iterations": smoothing_iterations,
        "smoothing_kernel": "5 x 5 focal mean", "slope_threshold_pct": slope_threshold_pct,
        "boundary": "observed dry cells 8-adjacent to a flooded cell",
        "coastal_filter": "boundary cells at or next to elevation <= 0 m removed (v2.0)",
        "slope_filter": "boundary cells with slope below the threshold removed (v2.1)",
        "allocation": "nearest boundary cell, Euclidean distance on the ground",
        "negative_depth": "set to 0", "grid": "the DEM's (extent resampled by nearest neighbour)",
        "departures_from_reference_gee_script": [
            "slope filter removes cells BELOW the threshold, as the paper states (the script keeps them)",
            "coastal filter applied, as the paper states (the script has none)",
            "no final convolution of the depth (the paper describes none)"],
    }
    depth_stats = R.summarise(depth, value_range=(0.0, max(1.0, float(np.nanmax(depth)))), bins=20)
    inputs = [
        F.item("dem_surface_model", "GLO-30 is a surface model: buildings, infrastructure and "
               "vegetation are in its heights, so depth is low where they stand inside the flood "
               "and the water edge reads high where they line it.", None, "", "stated_by_source",
               "2022"),
        F.item("dem_relative_le90", "GLO-30 relative vertical accuracy < 2 m (slope <= 20 %) and "
               "< 4 m (slope > 20 %), 90 % point-to-point error within 1 x 1 degree, global "
               "mean -- local deviations occur (Product Handbook v5.0).", [2.0, 4.0], "m",
               "stated_by_source", "2022"),
        F.item("dem_absolute_le90", "Absolute vertical accuracy < 4 m (90 % linear error); it "
               "cancels out of depth and is not perturbed.", ABS_LE90_M, "m", "stated_by_source",
               "2022"),
    ]
    tool_items = [
        F.item("fwdet_parameters", f"{smoothing_iterations} smoothing iterations and a "
               f"{slope_threshold_pct} % slope threshold -- the paper's optimum 'for most DEMs'; "
               "GLO-30 was not among the DEMs it tested (its 30 m NASADEM optimum was 10 and "
               "2.5 %).", [smoothing_iterations, slope_threshold_pct], "", "assumed"),
        F.item("boundary_cells", f"{counts['cells_used']} of {counts['cells_initial']} boundary "
               f"cells used ({counts['removed_coastal']} removed at or next to 0 m, "
               f"{counts['removed_slope']} below the slope threshold).", counts["cells_used"],
               "cells", "measured"),
        F.item("boundary_definition", "Boundary cells are observed dry cells next to the flood; "
               "unobserved cells and permanent water are not taken as shoreline, and the "
               "smoothing is masked back to the boundary after each pass -- choices the paper "
               "leaves open.", None, "", "assumed"),
        F.item("edge_above_flood_ground_m", f"The boundary cells' median elevation is "
               f"{counts.get('edge_above_flood_ground_m')} m above the flooded cells' median "
               "ground. GLO-30 is a surface model: where the edge is tree-covered or built up, "
               "the water surface is read off canopy or roofs and the depth is biased high.",
               counts.get("edge_above_flood_ground_m"), "m", "measured"),
        F.item("depth_bias", f"Depth is reported as {BIAS_LABEL} (BUG-014, user decision "
               f"2026-10-26): {BIAS_REASON}. Evidence: the edge stands "
               f"{counts.get('edge_above_flood_ground_m')} m above the flooded ground (medians).",
               BIAS_LABEL, "", "assumed"),
        F.item("negative_depth", f"{run['negative_share']:.4f} of the flooded cells came out "
               "below their water surface's ground and were set to 0 m.", run["negative_share"],
               "fraction", "measured"),
        F.item("extent_resampled", f"The extent ({resampled['source_crs']}, "
               f"{resampled['source_resolution']}) was put on the DEM grid by nearest neighbour: "
               f"{round(int(flooded.sum()) * px_km2, 4)} km2 flooded on that grid.",
               round(int(flooded.sum()) * px_km2, 4), "km2", "measured"),
        F.item("error_realisations", f"{realisations} DEM perturbations (seed {seed}) x 3 "
               "boundaries (as mapped, +1 px, -1 px); a pixel the eroded boundary leaves dry "
               "counts as 0 m.", realisations * 3, "runs", "measured"),
        F.item("error_model", "DEM error per pixel ~ normal with sigma = LE90 / 1.645 from the "
               f"relative accuracy by slope class; spatial correlation {error_correlation_px} px "
               "(0 = independent pixels). The slope filter uses the unperturbed DEM in every "
               "realisation.", error_correlation_px, "px", "assumed"),
        F.item("vehicle_band_unstable", f"{unstable_vehicle:.4f} of the flooded area has a "
               "probability above 0.5 of changing vehicle class (0.3 / 0.6 m) across the "
               "realisations.", round(unstable_vehicle, 4), "fraction", "measured"),
        F.item("advisory_band_unstable", f"{unstable_advisory:.4f} of the flooded area has a "
               "probability above 0.5 of crossing 10 cm across the realisations.",
               round(unstable_advisory, 4), "fraction", "measured"),
        F.item("precision_not_accuracy", "The spread says how sensitive the depth is to the DEM "
               "and the boundary; no observed depth exists for the AOI, so it is not an accuracy.",
               None, "", "stated_by_source"),
    ]
    if not person:
        tool_items.append(F.item("person_bands_pending", "The person depth bands (D-25) are not "
                                 "computed: the average adult height and the body-segment "
                                 "proportions have not been read from their sources.", None, "",
                                 "measured"))
    steps = [
        ("read the DEM", f"GLO-30 over the AOI, {len(urls)} tile(s), {res:.8f} deg"),
        ("extent onto the DEM grid", f"{product} extent, nearest neighbour"),
        ("boundary cells", f"{counts['cells_initial']} found; {counts['removed_coastal']} coastal "
         f"and {counts['removed_slope']} low-slope removed; {counts['cells_used']} used"),
        ("smooth the boundary", f"5 x 5 focal mean x {smoothing_iterations}"),
        ("water surface and depth", f"nearest boundary elevation minus the DEM over "
         f"{counts['flooded_cells']} flooded cells"),
        ("depth error", f"{realisations} DEM perturbations x 3 boundaries"),
        ("bands", "vehicle classes 0.3 / 0.6 m (D-36), 10 cm advisory (D-16)"),
    ]
    return {
        "product": product, "extent_raster": extent_raster, "bbox": bbox, "grid": plan.as_dict(),
        "method": method, "boundary": counts, "extent_on_dem_grid": resampled,
        "flooded": {"cells": int(flooded.sum()), "km2_on_dem_grid": round(int(flooded.sum()) * px_km2, 4)},
        "depth": {"unit": "m", "stats": depth_stats,
                  "median_m": round(float(np.nanmedian(depth)), 3),
                  "p95_m": round(float(np.nanpercentile(depth, 95)), 3),
                  "max_m": round(float(np.nanmax(depth)), 3),
                  "negative_set_to_zero_share": round(run["negative_share"], 6),
                  "raster": rasters.get("depth")},
        "bias": {"direction": "high", "label": BIAS_LABEL, "reason": BIAS_REASON,
                 "evidence_edge_above_flood_ground_m": counts.get("edge_above_flood_ground_m"),
                 "magnitude": "not measured",
                 "decided": "user, 2026-10-26 (BUG-014): keep GLO-30, report as biased high"},
        "vehicle_classes": _classes(depth, prob_vehicle, px_km2),
        "vehicle_source": "Kramer et al. (2016), D-36",
        "advisory_10cm": {"threshold_m": ADVISORY_M, "km2_at_or_above": round(int(at_or_above.sum()) * px_km2, 4),
                          "share_at_or_above": round(int(at_or_above.sum()) / nf, 4),
                          "source": "design decision D-16"},
        "person_bands": (person if person else
                         {"status": "not_known", "reason": "the average adult height and the "
                          "body-segment proportions (D-25) are not read from their sources yet"}),
        "error": {"realisations": realisations, "seed": seed, "boundary_variants": list(BOUNDARY_SHIFTS),
                  "dem_error": {"relative_le90_m": {"slope_le_20pct": 2.0, "slope_gt_20pct": 4.0},
                                "sigma_rule": "LE90 / 1.645 (normal assumed)",
                                "correlation_px": error_correlation_px},
                  "median_sd_m": round(float(np.nanmedian(err["sd"])), 3),
                  "mean_p05_p95_width_m": round(float(np.nanmean(err["p95"] - err["p05"])), 3),
                  "unstable_share_vehicle": round(unstable_vehicle, 4),
                  "unstable_share_advisory": round(unstable_advisory, 4),
                  "rasters": {k: v for k, v in rasters.items() if k != "depth"}},
        "method_steps": [{"step": s, "did": d} for s, d in steps],
        "uncertainty": F.blocks(inputs, tool_items),
        "sources": [
            F.source("cohen-2022-fwdet", "Sensitivity of Remote Sensing Floodwater Depth "
                     "Calculation to Boundary Filtering and Digital Elevation Model Selections "
                     "(FwDET v2.1)", "Cohen et al., Remote Sensing 14(21) 5313",
                     "https://doi.org/10.3390/rs14215313", "2022", "CC BY 4.0"),
            F.source("copernicus-dem-glo30", "Copernicus DEM GLO-30 (Product Handbook v5.0 for "
                     "its accuracy)", "ESA / Airbus", "https://doi.org/10.5270/ESA-c5d3d65",
                     "2022", role="major"),
            F.source("kramer-2016", "Safety criteria for the trafficability of inundated roads "
                     "in urban floodings", "Kramer et al., IJDRR 17", "https://doi.org/10.1016/"
                     "j.ijdrr.2016.04.003", "2016", role="supporting"),
        ],
    }
