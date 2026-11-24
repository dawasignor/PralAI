#!/usr/bin/env python
"""The three Earth-observation workflows: Sentinel-2, terrain and water.

    python tests/test_lulc_workflows.py            # live: hits three catalogues
    python tests/test_lulc_workflows.py --offline  # pure functions only

Checks, in order:

    1. registry   -- all three are registered and discoverable
    2. tiling     -- GSW and GLO-30 tile names across all four hemispheres
    3. slope      -- Horn's method against a ramp whose slope is known exactly
    4. indices    -- normalised difference, NaN handling, the NDMI/NDBI identity
    5. grid       -- an angular grid refuses to invent a metric pixel area
    6. ranges     -- no random reflectance pushes an index out of its range
    7. scaling    -- missing reflectance metadata raises, never defaults
    8. roundtrip  -- a written index reads back as the values that went in
    8b. coverage  -- a scene that misses part of the AOI is refused, not clipped
    8c. grid      -- the AOI window is planned once per MGRS tile, not per scene
    8d. probe     -- scenes are probed at 1/8, the accepted one measured in full
    8e. mosaic    -- two UTM tiles paste onto one lattice; a third zone is refused
    8f. datatake  -- one pass is judged by its datatake, never by the timestamps
    8g. lattice   -- pixel-is-point tiles mosaic with no gap and no half-pixel shift
    8h. landmask  -- the sea is excluded, inland water is not, and the denominator moves
    8i. selection -- scene selection judges the land, not the sea (single and mosaic)
    9. live       -- Sentinel-2 scene selection, JRC water, Copernicus DEM
"""

from __future__ import annotations

import inspect
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import os
os.environ.setdefault("MEMORY_WRITE", "0")
os.environ.setdefault("EXCHANGE_LOG", "off")
os.environ.setdefault("EXCHANGE_LOG_ECHO", "0")

import numpy as np

# Shillong, Meghalaya -- monsoon, mountainous, and the worst case for optical
# imagery, which is exactly why it is the fixture.
BBOX = [91.80, 25.50, 91.95, 25.62]
START = "2025-08-03"

# The live Sentinel-2 fixture runs at the tool's DEFAULTS, always (user
# decision 2026-10-26, BUG-003): min_valid_fraction 0.7, the default lookback
# and probes. Shillong in monsoon cannot pass at 0.7, and the test used to
# lower the threshold to 0.5 to make it -- a relaxed default the rest of the
# project forbids. Edinburgh (the generic test box) passes at the defaults: the
# 11 Aug 2026 scene is 0.95 clear over its land. `check_no_relaxed_live_calls`
# keeps any test from lowering it again.
S2_BBOX = [-3.25, 55.90, -3.10, 56.00]
S2_START = "2026-08-14"


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def fail(msg: str):
    raise SystemExit(f"\n  FAIL: {msg}")


# ---------------------------------------------------------------------------
# 1. registry
# ---------------------------------------------------------------------------
def check_registry():
    from core import workflows

    expected = {"fetch_lulc_data", "fetch_terrain_data", "fetch_permanent_water"}
    missing = expected - set(workflows.names())
    if missing:
        fail(f"not registered: {sorted(missing)}; found {workflows.names()}")
    print(f"  registered: {sorted(expected)}")


# ---------------------------------------------------------------------------
# 2. tile naming
# ---------------------------------------------------------------------------
def check_tiling():
    from core.workflows.terrain import tile_url as dem_tile
    from core.workflows.water import tile_url as gsw_tile

    # GLO-30 tiles are named for their SOUTH-WEST corner; GSW for its
    # NORTH-WEST corner. Getting either backwards silently returns a tile from
    # somewhere else on Earth, which is why both hemispheres are checked.
    dem_cases = [
        (25.56, 91.88, "N25_00_E091_00"),
        (-1.20, -60.50, "S02_00_W061_00"),
        (51.50, -0.12, "N51_00_W001_00"),
    ]
    for lat, lon, want in dem_cases:
        got = dem_tile(lat, lon)
        if want not in got:
            fail(f"GLO-30 tile for ({lat}, {lon}): expected {want} in {got}")
    print(f"  GLO-30: {len(dem_cases)} tiles correct across hemispheres")

    gsw_cases = [
        (25.56, 91.88, "occurrence_90E_30N"),
        (-5.00, -60.50, "occurrence_70W_0N"),
        (-15.00, 25.00, "occurrence_20E_10S"),
    ]
    for lat, lon, want in gsw_cases:
        got = gsw_tile(lat, lon)
        if want not in got:
            fail(f"GSW tile for ({lat}, {lon}): expected {want} in {got}")
    print(f"  JRC GSW: {len(gsw_cases)} tiles correct across hemispheres")


# ---------------------------------------------------------------------------
# 3. slope, against a known answer
# ---------------------------------------------------------------------------
def check_slope():
    from core.workflows.terrain import classify_slope, slope_degrees

    # A planar ramp rising 1 m per metre eastward is exactly 45 degrees. Build
    # it in metres, then convert to the angular spacing the function expects,
    # so the cos(latitude) correction is exercised rather than bypassed.
    lat = 25.56
    res_deg = 1.0 / 3600.0
    dx = res_deg * 111320.0 * math.cos(math.radians(lat))

    cols = np.arange(40, dtype="float32")
    ramp = np.tile(cols * dx, (30, 1))            # rises dx metres per column
    slope = slope_degrees(ramp, lat, res_deg)
    interior = slope[1:-1, 1:-1]

    if not np.allclose(interior, 45.0, atol=1e-3):
        fail(f"45-degree ramp gave {np.nanmin(interior)}..{np.nanmax(interior)}")
    print(f"  1:1 ramp -> {interior.mean():.4f} degrees (expected 45.0)")

    # Flat ground is zero everywhere, and the un-evaluable ring is NaN.
    flat = slope_degrees(np.ones((20, 20), dtype="float32"), lat, res_deg)
    if not np.allclose(flat[1:-1, 1:-1], 0.0):
        fail("flat terrain did not give zero slope")
    if np.isfinite(flat[0, :]).any() or np.isfinite(flat[:, 0]).any():
        fail("outer ring should be NaN -- a 3x3 kernel cannot reach it")
    print("  flat -> 0.0 degrees, outer ring NaN")

    # A half-degree slope is 'flat' (<2); 45 degrees is 'very_steep' (>=30).
    # (Restored 2026-11-21 after two days on 5-degree bands.)
    codes = classify_slope(np.array([[0.5, 3.0, 10.0, 20.0, 45.0]]))
    if list(codes[0]) != [1, 2, 3, 4, 5]:
        fail(f"slope classes wrong: {list(codes[0])}")
    print("  class boundaries: flat/gentle/moderate/steep/very_steep")


# ---------------------------------------------------------------------------
# 4. indices
# ---------------------------------------------------------------------------
def check_indices():
    from core.workflows.sentinel import _normalised_difference, compute_indices

    a = np.array([[0.4, 0.0, 0.5]], dtype="float32")
    b = np.array([[0.1, 0.0, np.nan]], dtype="float32")
    nd = _normalised_difference(a, b)

    if not math.isclose(float(nd[0, 0]), 0.6, rel_tol=1e-5):
        fail(f"(0.4-0.1)/(0.4+0.1) should be 0.6, got {nd[0, 0]}")
    if np.isfinite(nd[0, 1]):
        fail("a zero denominator must be NaN, not inf")
    if np.isfinite(nd[0, 2]):
        fail("NaN input must propagate")
    print("  normalised difference: value, zero denominator, NaN propagation")

    # BUI spans [-2, 2] and must equal NDBI - NDVI exactly.
    #
    # TODO(arid-bui): this checks the arithmetic, not whether BUI means what
    # the built-up classification claims. The synthetic pixels here are forest
    # and concrete; bare ground, where BUI fails, is not covered. See
    # docs/arid_region.md.
    bands = {
        "red":    np.array([[0.03, 0.22]], dtype="float32"),
        "nir":    np.array([[0.40, 0.25]], dtype="float32"),
        "nir08":  np.array([[0.42, 0.26]], dtype="float32"),
        "swir16": np.array([[0.15, 0.32]], dtype="float32"),
        "swir22": np.array([[0.08, 0.28]], dtype="float32"),
        "green":  np.array([[0.05, 0.20]], dtype="float32"),
    }
    out = compute_indices(bands, ["ndvi", "ndbi", "bui", "ndmi", "nmdi", "mndwi"])
    if not np.allclose(out["bui"], out["ndbi"] - out["ndvi"]):
        fail("BUI must be exactly NDBI - NDVI")

    forest_bui, built_bui = float(out["bui"][0, 0]), float(out["bui"][0, 1])
    if not forest_bui < -1.0 < built_bui:
        fail(f"forest {forest_bui} should sit far below built-up {built_bui}")
    print(f"  BUI: forest {forest_bui:.3f} vs built-up {built_bui:.3f} "
          f"(margin {built_bui - forest_bui:.3f})")

    # The documented redundancy: on a shared band pair NDMI is exactly -NDBI.
    # Here they differ only because NDMI uses B08 and NDBI uses B8A, so they
    # are near-negatives. This asserts the claim the docs make.
    same_pair = _normalised_difference(bands["nir08"], bands["swir16"])
    if not np.allclose(same_pair, -out["ndbi"], atol=1e-6):
        fail("NDMI on NDBI's band pair should be exactly -NDBI")
    gap = float(np.abs(out["ndmi"] - (-out["ndbi"])).max())
    if not 0 < gap < 0.1:
        fail(f"NDMI/NDBI should be near-negatives, differing slightly; got {gap}")
    print(f"  NDMI == -NDBI on a shared pair; B08/B8A split differs by {gap:.4f}")


# ---------------------------------------------------------------------------
# 5. the angular-grid guard
# ---------------------------------------------------------------------------
def check_grid_guard():
    from core.workflows import _raster as R

    # Squaring a resolution in degrees does not give an area. A geographic grid
    # with no explicit pixel area must refuse rather than return a wrong number.
    angular = R.GridPlan(None, None, None, (10, 10), 0.00025, units="deg")
    try:
        angular.pixel_area_km2
    except ValueError:
        print("  angular grid without an explicit area raises, as it must")
    else:
        fail("a degree-spaced grid silently squared its resolution into an area")

    metric = R.GridPlan(None, None, None, (10, 10), 20.0, units="m")
    if not math.isclose(metric.pixel_area_km2, 0.0004):
        fail(f"20 m pixel should be 0.0004 km^2, got {metric.pixel_area_km2}")
    print("  metric grid: 20 m pixel -> 0.0004 km^2")

    # coverage is what everything downstream reads first.
    arr = np.array([[0.5, np.nan], [0.1, 0.3]], dtype="float32")
    cov = R.summarise(arr)["coverage"]
    if cov["valid_fraction"] != 0.75 or cov["valid_pixels"] != 3:
        fail(f"valid_fraction should be 0.75 over 3 pixels, got {cov}")
    empty = R.summarise(np.full((4, 4), np.nan, dtype="float32"))
    if empty["coverage"]["valid_fraction"] != 0.0 or empty["mean"] is not None:
        fail("an all-NaN array must give valid_fraction 0 and a null mean")
    print("  summarise: valid_fraction 0.75, all-NaN -> null mean not 0.0")

    # Saturation is counted only when a theoretical bound is supplied, and
    # "not measured" must stay distinguishable from "none found".
    stuck = np.array([[-1.0, 1.0, 0.25, 0.5]], dtype="float32")
    if cov["saturated_pixels"] is not None:
        fail("saturation must be null when no bound was given, not 0")
    counted = R.summarise(stuck, saturating_at=(-1.0, 1.0))["coverage"]
    if counted["saturated_pixels"] != 2 or counted["saturated_fraction"] != 0.5:
        fail(f"expected 2 of 4 pixels saturated, got {counted}")
    clean = R.summarise(stuck, saturating_at=(-2.0, 2.0))["coverage"]
    if clean["saturated_pixels"] != 0:
        fail(f"nothing sits on +-2, expected 0, got {clean}")
    print("  saturation: null unmeasured, 2/4 on the bound, 0 off it")


def check_aoi_coverage(tmp_dir: str):
    """A scene that does not cover the whole AOI must be refused, not truncated.

    plan_grid clamps its window to the raster, so an AOI straddling two MGRS
    tiles used to come back as statistics over whichever part fell inside the
    selected scene -- with nothing in the response saying the bbox had not been
    honoured. terrain and water already raise on a multi-tile bbox; this is the
    Sentinel-2 equivalent.
    """
    import rasterio
    from rasterio.transform import from_origin

    from core.workflows import _raster as R
    from core.workflows import sentinel as S

    # A tile covering [91.80, 25.50] -> [91.90, 25.60], all SCL 4 (vegetation).
    path = str(Path(tmp_dir) / "scl.tif")
    with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000,
                       count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(91.80, 25.60, 0.0001, 0.0001)) as dst:
        dst.write(np.full((1000, 1000), 4, dtype="uint8"), 1)

    inside = [91.82, 25.52, 91.88, 25.58]
    overhanging = [91.85, 25.52, 91.95, 25.58]       # half of it is off the tile

    if R.plan_grid(path, inside).coverage != 1.0:
        fail("a fully contained AOI must report coverage 1.0")
    partial = R.plan_grid(path, overhanging)
    if not math.isclose(partial.coverage, 0.5, abs_tol=0.05):
        fail(f"half-off-tile AOI should report ~0.5 coverage, got {partial.coverage}")
    if partial.bounds_wgs84()[2] >= overhanging[2]:
        fail("bounds_wgs84 must report the truncated extent, not the request")
    print(f"  contained AOI 1.0, overhanging AOI {partial.coverage}")
    print(f"  bbox_covered {partial.bounds_wgs84()} != requested {overhanging}")

    class _Item:
        def __init__(self, href, id_="SCENE_A", tile="MGRS-46RCP"):
            self.id = id_
            self.assets = {"scl": type("A", (), {"href": href})()}
            self.properties = {"datetime": "2025-05-10T04:41:00Z",
                               "eo:cloud_cover": 5.0}
            if tile:
                self.properties["grid:code"] = tile

    try:
        S.select_scene([_Item(path)], overhanging, 0.7, 12)
    except ValueError as exc:
        if "MGRS" not in str(exc):
            fail(f"partial-coverage error should name the MGRS cause: {exc}")
        print("  partial scene rejected, and the error names the cause")
    else:
        fail("a scene covering half the AOI was accepted and silently truncated")

    # Lowering the bar is allowed, but only deliberately, and it is recorded.
    _, plan, _, attempts = S.select_scene([_Item(path)], overhanging, 0.7, 12,
                                          min_aoi_coverage=0.4)
    if attempts[-1]["aoi_coverage"] != round(plan.coverage, 4):
        fail(f"the accepted attempt must record its coverage: {attempts[-1]}")
    print(f"  min_aoi_coverage=0.4 accepts it, recording {plan.coverage}")


def check_grid_reuse(tmp_dir: str):
    """The AOI window is computed once per MGRS tile, not once per scene.

    Every scene of one tile shares an extent, CRS and pixel alignment, so the
    window is identical across dates -- 13 of 13 candidates over Shillong were
    MGRS-46RCP. Re-deriving it per candidate costs a COG open each time.

    The fallback matters as much as the cache: a scene from a DIFFERENT tile,
    or one the catalogue did not identify, must compute its own grid rather
    than borrow a neighbour's, or it reads the wrong pixels.
    """
    import rasterio
    from rasterio.transform import from_origin

    from core.workflows import _raster as R
    from core.workflows import sentinel as S

    def _tile(name: str, west: float, north: float) -> str:
        path = str(Path(tmp_dir) / f"{name}.tif")
        with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000,
                           count=1, dtype="uint8", crs="EPSG:4326",
                           transform=from_origin(west, north, 0.0001, 0.0001)) as dst:
            dst.write(np.full((1000, 1000), 4, dtype="uint8"), 1)
        return path

    # Two adjacent tiles. The AOI sits inside the first one.
    tile_a = _tile("a", 91.80, 25.60)
    tile_b = _tile("b", 91.90, 25.60)
    aoi = [91.82, 25.52, 91.88, 25.58]

    calls: list[str] = []
    real_plan_grid = R.plan_grid

    def counting_plan_grid(href, bbox, **kw):
        calls.append(href)
        return real_plan_grid(href, bbox, **kw)

    class _Item:
        def __init__(self, href, id_, tile):
            self.id = id_
            self.assets = {"scl": type("A", (), {"href": href})()}
            self.properties = {"datetime": "2025-05-10T04:41:00Z",
                               "eo:cloud_cover": 5.0}
            if tile:
                self.properties["grid:code"] = tile

    R.plan_grid = counting_plan_grid
    try:
        # Five scenes, one tile, none clear enough to accept -> five probes.
        five = [_Item(tile_a, f"S{i}", "MGRS-46RCP") for i in range(5)]
        try:
            S.select_scene(five, aoi, 1.01, 12)      # unreachable threshold
        except ValueError:
            pass
        if len(calls) != 1:
            fail(f"one tile over 5 scenes should plan once, planned {len(calls)}")
        print(f"  5 scenes, 1 tile -> {len(calls)} grid computation")

        # A second tile, and an unidentified scene, must each compute their own.
        calls.clear()
        mixed = [_Item(tile_a, "S_A", "MGRS-46RCP"),
                 _Item(tile_a, "S_A2", "MGRS-46RCP"),
                 _Item(tile_b, "S_B", "MGRS-46RDP"),
                 _Item(tile_a, "S_X", None)]          # no grid:code
        try:
            S.select_scene(mixed, aoi, 1.01, 12)
        except ValueError:
            pass
        if len(calls) != 3:
            fail(f"tile A + tile B + an unidentified scene should plan 3 times, "
                 f"planned {len(calls)}")
        print(f"  tile A x2, tile B, unidentified -> {len(calls)} computations")

        # The fallback is not merely counted -- the borrowed grid must be right.
        plan_a = real_plan_grid(tile_a, aoi)
        if S._tile_key(_Item(tile_a, "S", "MGRS-46RCP")) != "MGRS-46RCP":
            fail("grid:code should be used as the tile key")
        if S._tile_key(_Item(tile_a, "S", None)) is not None:
            fail("a scene with no tile identity must return None, never a guess")
        print(f"  unidentified scene -> tile key None (grid {plan_a.shape} recomputed)")
    finally:
        R.plan_grid = real_plan_grid


def check_probe_decimation(tmp_dir: str):
    """Probes read SCL at 1/N; the accepted scene is confirmed at full res.

    The estimate only decides whether a scene is worth confirming. Every number
    that leaves select_scene must be measured, never estimated -- otherwise a
    valid_fraction that Entro consumes would carry the decimation error.
    """
    import rasterio
    from rasterio.transform import from_origin

    from core.workflows import _raster as R
    from core.workflows import sentinel as S

    def _tile(name: str, cloud_rows: int) -> str:
        """1000x1000 SCL tile: `cloud_rows` rows of cloud (9), rest vegetation."""
        arr = np.full((1000, 1000), 4, dtype="uint8")
        if cloud_rows:
            arr[:cloud_rows, :] = 9
        path = str(Path(tmp_dir) / f"{name}.tif")
        with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000,
                           count=1, dtype="uint8", crs="EPSG:4326",
                           transform=from_origin(91.80, 25.60, 0.0001, 0.0001)) as dst:
            dst.write(arr, 1)
        return path

    aoi = [91.82, 25.52, 91.88, 25.58]        # 600x600 of the tile

    # A decimated read keeps the class codes and the proportions.
    clear = _tile("clear", 0)
    plan = R.plan_grid(clear, aoi)
    full = R.read_classes(clear, plan)
    dec = R.read_classes(clear, plan, decimation=8)
    if dec.shape[0] >= full.shape[0] or dec.shape[1] >= full.shape[1]:
        fail(f"decimated read should be smaller: {dec.shape} vs {full.shape}")
    if set(np.unique(dec)) - set(np.unique(full)):
        fail("decimation invented a class code that is not in the full read")
    print(f"  decimation 1/8: {full.shape} -> {dec.shape}, codes preserved")

    class _Item:
        def __init__(self, href, id_):
            self.id = id_
            self.assets = {"scl": type("A", (), {"href": href})()}
            self.properties = {"datetime": "2025-05-10T04:41:00Z",
                               "eo:cloud_cover": 5.0, "grid:code": "MGRS-46RCP"}

    # A clear scene is accepted, and its record is a measurement.
    item, plan, scl, attempts = S.select_scene(
        [_Item(clear, "CLEAR")], aoi, 0.9, 12, probe_decimation=8)
    rec = attempts[-1]
    if rec.get("estimated") is not False:
        fail(f"an accepted scene must be marked measured, not estimated: {rec}")
    if "probe_valid_fraction" not in rec:
        fail(f"the accepted record should keep the probe estimate: {rec}")
    if scl.shape != plan.shape:
        fail(f"the returned SCL must be full resolution: {scl.shape} vs {plan.shape}")
    print(f"  accepted: vf={rec['aoi_valid_fraction']} measured, "
          f"probe estimated {rec['probe_valid_fraction']}, SCL {scl.shape}")

    # A cloudy scene is rejected on the cheap probe, without a full read.
    cloudy = _tile("cloudy", 500)             # half the tile is cloud
    try:
        S.select_scene([_Item(cloudy, "CLOUDY")], aoi, 0.9, 12,
                       probe_decimation=8)
    except ValueError:
        rejected = True
    else:
        rejected = False
    if not rejected:
        fail("a half-clouded scene must not pass a 0.9 threshold")
    print("  cloudy scene rejected on the 1/8 probe")

    # Decimation off must give the same decision.
    item2, _, _, att2 = S.select_scene(
        [_Item(clear, "CLEAR")], aoi, 0.9, 12, probe_decimation=1)
    if item2.id != item.id:
        fail("decimated and full-resolution probing chose different scenes")
    if att2[-1]["aoi_valid_fraction"] != rec["aoi_valid_fraction"]:
        fail(f"valid fraction must not depend on probe_decimation: "
             f"{att2[-1]['aoi_valid_fraction']} vs {rec['aoi_valid_fraction']}")
    if att2[-1].get("estimated") is not False:
        fail("probe_decimation=1 should still mark the record measured")
    print(f"  probe_decimation=1 gives the same scene and the same "
          f"vf={att2[-1]['aoi_valid_fraction']}")


def check_land_denominator(tmp_dir: str):
    """Scene selection judges the land, not the sea.

    Until 2026-09-26 the 0.7 threshold was applied over the whole window while
    the reported figure was land only, so a clear sea could carry a cloudy
    coast over the threshold: Bangkok was selected on 0.8686 and reported
    0.8243 of its land. Two synthetic scenes, the western half of the AOI sea:

        SEA_CLEAR  sea clear, land half cloud  -> window 0.75, land 0.50
        LAND_CLEAR sea all cloud, land clear   -> window 0.50, land 1.00

    With the land mask the newer SEA_CLEAR must be REJECTED and LAND_CLEAR
    accepted, on the probe and the full-resolution confirmation alike --
    exactly the opposite of what counting the sea decides. Checked for
    select_scene and select_mosaic, which share the measurement.
    """
    import rasterio
    from rasterio.transform import from_origin

    from core.workflows import sentinel as S

    def _tile(name: str, west: float) -> str:
        """A clear 1000x1000 SCL tile; `paint` decides what it shows."""
        arr = np.full((1000, 1000), 4, dtype="uint8")
        path = str(Path(tmp_dir) / f"{name}.tif")
        with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000,
                           count=1, dtype="uint8", crs="EPSG:4326",
                           transform=from_origin(west, 25.60, 0.0001, 0.0001)) as dst:
            dst.write(arr, 1)
        return path

    def _write(path: str, fill) -> None:
        with rasterio.open(path, "r+") as dst:
            arr = dst.read(1)
            fill(arr, dst.transform)
            dst.write(arr, 1)

    class _Item:
        def __init__(self, href, id_, day, tile="MGRS-46RCP"):
            self.id = id_
            self.assets = {"scl": type("A", (), {"href": href})()}
            self.properties = {"datetime": f"{day}T04:41:00Z",
                               "eo:cloud_cover": 5.0, "grid:code": tile}

    midline = 91.85                     # west of it is "sea" in every test

    def paint(sea_code: int, land_rows_cloud: float):
        """Fill: sea side all `sea_code`; land side cloud on the top share."""
        def fill(arr, transform):
            col_mid = int(round((midline - transform.c) / transform.a))
            col_mid = min(max(col_mid, 0), arr.shape[1])
            arr[:, :col_mid] = sea_code
            arr[:, col_mid:] = 4
            # Cloud over the land half of the AOI rows (25.52-25.58 is rows
            # 200-800), so the share is exact inside the window.
            n = int(600 * land_rows_cloud)
            arr[200:200 + n, col_mid:] = 9
        return fill

    def land_west_is_sea(plan):
        """Reporting domain: everything east of the midline."""
        xs = plan.transform.c + (np.arange(plan.shape[1]) + 0.5) * plan.transform.a
        return np.broadcast_to(xs >= midline, plan.shape).copy()

    # -- select_scene ----------------------------------------------------
    aoi = [91.82, 25.52, 91.88, 25.58]                 # midline at its centre
    sea_clear = _tile("sea_clear", 91.80)
    _write(sea_clear, paint(sea_code=4, land_rows_cloud=0.5))
    land_clear = _tile("land_clear", 91.80)
    _write(land_clear, paint(sea_code=9, land_rows_cloud=0.0))
    items = [_Item(sea_clear, "SEA_CLEAR", "2025-05-20"),
             _Item(land_clear, "LAND_CLEAR", "2025-05-10")]

    item, _, _, att = S.select_scene(items, aoi, 0.7, 12)
    if item.id != "SEA_CLEAR":
        fail(f"without a land mask the clear sea should carry SEA_CLEAR "
             f"(the old behaviour), got {item.id}")
    print(f"  no mask   : SEA_CLEAR accepted at {att[-1]['aoi_valid_fraction']} "
          f"of the window -- the sea voting")

    item, _, _, att = S.select_scene(items, aoi, 0.7, 12,
                                     land=land_west_is_sea)
    if item.id != "LAND_CLEAR":
        fail(f"with the land mask LAND_CLEAR must win, got {item.id}: {att}")
    first, last = att[0], att[-1]
    if not math.isclose(first["aoi_valid_fraction"], 0.5, abs_tol=0.03):
        fail(f"SEA_CLEAR's land fraction should be ~0.5, got {first}")
    if last["aoi_valid_fraction"] != 1.0 or last.get("estimated") is not False:
        fail(f"LAND_CLEAR should be confirmed at 1.0 of land: {last}")
    if {a.get("valid_fraction_is_of") for a in att} != {"land"}:
        fail(f"every record must say its fraction is of the land: {att}")
    print(f"  land mask : SEA_CLEAR rejected at {first['aoi_valid_fraction']} of "
          f"land, LAND_CLEAR accepted at {last['aoi_valid_fraction']}")

    # -- select_mosaic: the same two cases across a tile boundary ----------
    aoi2 = [91.85 - 0.03, 25.52, 91.85 + 0.03, 25.58]
    west_a = _tile("m_a_new", 91.75)     # covers 91.75-91.85
    east_a = _tile("m_b_new", 91.85)     # covers 91.85-91.95
    west_b = _tile("m_a_old", 91.75)
    east_b = _tile("m_b_old", 91.85)
    _write(west_a, paint(sea_code=4, land_rows_cloud=0.5))   # all sea side
    _write(east_a, paint(sea_code=4, land_rows_cloud=0.5))   # all land side
    _write(west_b, paint(sea_code=9, land_rows_cloud=0.0))
    _write(east_b, paint(sea_code=9, land_rows_cloud=0.0))
    pairs = [_Item(west_a, "A_W", "2025-05-20", "MGRS-46RCP"),
             _Item(east_a, "A_E", "2025-05-20", "MGRS-46RDP"),
             _Item(west_b, "B_W", "2025-05-10", "MGRS-46RCP"),
             _Item(east_b, "B_E", "2025-05-10", "MGRS-46RDP")]
    members, _, _, att = S.select_mosaic(pairs, aoi2, 0.7, 12,
                                         land=land_west_is_sea)
    chosen = sorted(m.id for m in members)
    if chosen != ["B_E", "B_W"]:
        fail(f"mosaic with the land mask should pick the land-clear date, "
             f"got {chosen}: {att}")
    if att[0].get("valid_fraction_is_of") != "land" or att[0].get("accepted"):
        fail(f"the sea-clear date must be rejected on land: {att[0]}")
    print(f"  mosaic    : sea-clear date rejected at {att[0]['aoi_valid_fraction']} "
          f"of land, land-clear date accepted at {att[-1]['aoi_valid_fraction']}")


def check_utm_mosaic(tmp_dir: str):
    """Sentinel-2's tiles are projected, and that changes what "aligned" means.

    The geographic mosaic (GLO-30, GSW) snaps to a global lattice because tile
    origins sit on whole degrees. MGRS has no global lattice: each UTM zone
    carries its own false easting, so two tiles are a whole number of pixels
    apart INSIDE one zone and an irrational number apart across a boundary.

    Four things have to hold, and the last two are the ones that would
    otherwise fail silently -- a misaligned paste returns an array of exactly
    the right shape whose values come from slightly the wrong place.
    """
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import transform_bounds

    from core.workflows import _raster as R

    res, size = 20.0, 400
    left, top = 399960.0, 800020.0          # a real T44N-series tile origin

    def tile(name, crs, x, y, value, *, n=size, r=res):
        path = str(Path(tmp_dir) / name)
        with rasterio.open(path, "w", driver="GTiff", height=n, width=n,
                           count=1, dtype="uint8", crs=crs, nodata=0,
                           transform=Affine(r, 0, x, 0, -r, y)) as dst:
            dst.write(np.full((n, n), value, dtype="uint8"), 1)
        return path

    west = tile("w.tif", "EPSG:32644", left, top, 4)
    east = tile("e.tif", "EPSG:32644", left + size * res, top, 5)

    # An AOI straddling the seam, expressed the way a caller expresses one.
    span = (left + 2000, top - 6000, left + size * res + 2000, top - 2000)
    bbox = list(transform_bounds("EPSG:32644", "EPSG:4326", *span))

    plan = R.plan_mosaic([west, east], bbox)
    if plan.units != "m" or str(plan.crs) != "EPSG:32644":
        fail(f"projected mosaic should stay in metres and in EPSG:32644, "
             f"got {plan.resolution} {plan.units} in {plan.crs}")
    if not math.isclose(plan.pixel_area_km2, (res ** 2) / 1e6, rel_tol=1e-9):
        fail(f"a metric pixel area is res^2, got {plan.pixel_area_km2}")

    codes = R.read_classes_mosaic([west, east], plan)
    if plan.coverage != 1.0:
        fail(f"two tiles spanning the AOI must cover all of it, "
             f"got {plan.coverage}")
    if set(np.unique(codes).tolist()) != {4, 5}:
        fail(f"the paste should carry both tiles' codes, got "
             f"{np.unique(codes).tolist()}")

    # The seam lands exactly on the tile boundary. A sub-pixel misalignment
    # shows up here and nowhere else.
    seam = int(round((left + size * res - plan.transform.c) / res))
    if set(np.unique(codes[:, :seam]).tolist()) != {4}:
        fail("pixels west of the seam must come only from the west tile")
    if set(np.unique(codes[:, seam:]).tolist()) != {5}:
        fail("pixels east of the seam must come only from the east tile")
    print(f"  two UTM tiles -> {codes.shape} at coverage {plan.coverage}, "
          f"seam clean at column {seam}")

    # A 10 m band decimates 2:1 onto the 20 m grid, and each tile keeps its own
    # scaling -- two scenes of one mosaic can carry different baselines, and
    # the BOA offset does not cancel out of a normalised difference.
    fine_w = tile("w10.tif", "EPSG:32644", left, top, 100, n=size * 2, r=res / 2)
    fine_e = tile("e10.tif", "EPSG:32644", left + size * res, top, 200,
                  n=size * 2, r=res / 2)
    band = R.read_band_mosaic([fine_w, fine_e], plan, per_tile=[
        {"scale": 0.01, "offset": 0.0, "nodata": 0},
        {"scale": 0.001, "offset": 0.0, "nodata": 0}])
    if not (np.allclose(band[:, :seam], 1.0)
            and np.allclose(band[:, seam:], 0.2)):
        fail("per-tile scaling was not applied per tile: "
             f"west {np.unique(band[:, :seam])}, east {np.unique(band[:, seam:])}")
    print(f"  10 m bands decimate 2:1 and keep their own scale "
          f"({np.unique(band[:, :seam])[0]} / {np.unique(band[:, seam:])[0]})")

    # The two refusals. Both would otherwise produce a plausible array.
    other_zone = tile("z.tif", "EPSG:32645", 200000.0, top, 6)
    try:
        R.plan_mosaic([west, other_zone], bbox)
        fail("a tile in another UTM zone must be refused, not reprojected")
    except ValueError as exc:
        if "projection" not in str(exc):
            fail(f"the cross-zone refusal should name the cause: {exc}")

    off_lattice = tile("o.tif", "EPSG:32644", left + size * res + 10.0, top, 7)
    try:
        R.plan_mosaic([west, off_lattice], bbox)
        fail("a tile half a pixel off the lattice must be refused")
    except ValueError as exc:
        if "whole number" not in str(exc):
            fail(f"the off-lattice refusal should name the cause: {exc}")
    print("  another UTM zone and a half-pixel shift are both refused")

    # And a gap is reported as a gap, never as a clean read.
    solo = R.plan_mosaic([west], bbox)
    R.read_classes_mosaic([west], solo)
    if not math.isclose(solo.coverage, 0.75, abs_tol=0.05):
        fail(f"one tile over a two-tile AOI should report ~0.75 coverage, "
             f"got {solo.coverage}")
    print(f"  a missing tile reports coverage {solo.coverage}, not 1.0")

    # Next to a zone boundary the other zone's tiles overlap the AOI (Valencia:
    # 31SBC/31SBD/31TBE with the zone-30 tiles). They are dropped when the
    # main zone's tiles cover it, and recorded; otherwise the tiling failure
    # is the typed NoUsableMosaic the WorldCover fallback answers.
    from core.workflows import sentinel as S

    class _Item:
        def __init__(self, href, tile):
            self.id = f"S2_{tile}"
            self.assets = {"scl": type("A", (), {"href": href})()}
            self.properties = {"datetime": "2025-05-10T04:41:00Z",
                               "eo:cloud_cover": 5.0, "grid:code": f"MGRS-{tile}"}

    group = [_Item(west, "44NAA"), _Item(east, "44NAB"), _Item(other_zone, "45NZZ")]
    members, plan, _, attempts = S.select_mosaic(group, bbox, 0.7, 12, probe_decimation=1)
    acc = attempts[-1]
    if not (acc.get("accepted") and [m.id for m in members] == ["S2_44NAA", "S2_44NAB"]
            and acc.get("dropped_tiles") == ["MGRS-45NZZ"] and acc.get("utm_zone_kept") == "44"
            and plan.coverage == 1.0):
        fail(f"the other zone's tile was not dropped and recorded: {acc}")
    print("  a tile from the neighbouring UTM zone is dropped and recorded; the zone-44 "
          "tiles cover the AOI")
    try:
        S.select_mosaic([_Item(west, "44NAA"), _Item(other_zone, "45NZZ")], bbox, 0.7, 12,
                        probe_decimation=1)
        fail("one zone-44 tile covering 0.75 of the AOI was accepted")
    except S.NoUsableMosaic as exc:
        if not any(a.get("dropped_tiles") for a in exc.attempts):
            fail(f"the failed group lost its drop record: {exc.attempts}")
    print("  when no single zone covers the AOI, the failure is NoUsableMosaic, with "
          "the drop on record")


def check_nonsquare_lattice(tmp_dir: str):
    """North of 50 deg a GLO-30 pixel is 1.5 x 1 arc-seconds (BUG-018).

    One resolution was kept for both axes, so over Edinburgh every terrain
    area came out 1.5x too large (157.4 km2 for a 104.9 km2 box), Horn's
    north-south step was stretched by 1.5 (slope low), the Water Body Mask
    was read onto square pixels (sea misplaced: 37.2 km2 instead of 24.8) and
    a two-tile mosaic would have pasted its tiles at the wrong rows. Checked:
    the pixel area, the mosaic's rows, the slope of a known plane, and that
    the AOI clip drops the padding ring and nothing else.
    """
    import rasterio
    from rasterio.transform import Affine

    from core.workflows import _raster as R
    from core.workflows import landmask as L
    from core.workflows.terrain import slope_degrees

    rx, ry = 1.5 / 3600.0, 1.0 / 3600.0
    size = 60
    west = -3.0

    def tile(name, south, values):
        path = str(Path(tmp_dir) / name)
        with rasterio.open(
                path, "w", driver="GTiff", height=size, width=size, count=1,
                dtype="float32", crs="EPSG:4326", nodata=-9999.0,
                transform=Affine(rx, 0, west - rx / 2, 0, -ry,
                                 south + size * ry + ry / 2)) as dst:
            dst.write(values.astype("float32"), 1)
        return path

    south_a = 55.0
    south_b = south_a + size * ry
    a = tile("a.tif", south_a, np.full((size, size), 1.0))
    b = tile("b.tif", south_b, np.full((size, size), 2.0))

    lat = south_a + size * ry / 2
    one = R.plan_grid(a, [west, south_a, west + 40 * rx, south_a + 40 * ry])
    want = (rx * 111320.0 * math.cos(math.radians(lat)) * ry * 111320.0) / 1e6
    if not math.isclose(one.pixel_area_km2, want, rel_tol=1e-3):
        fail(f"pixel area {one.pixel_area_km2} should be {want} (x and y spacing differ)")
    if not math.isclose(one.resolution_y, ry, rel_tol=1e-9):
        fail("plan_grid must carry the row spacing")
    print(f"  pixel area {one.pixel_area_km2 * 1e6:.1f} m2 from 1.5 x 1 arc-seconds")

    bbox = [west + 5 * rx, south_a + 10 * ry, west + 30 * rx, south_b + 10 * ry]
    plan = R.plan_mosaic([a, b], bbox)
    mosaic = R.read_band_mosaic([a, b], plan)
    rows_b = int(np.sum(mosaic[:, 0] == 2.0))
    rows_a = int(np.sum(mosaic[:, 0] == 1.0))
    # The box spans 50 rows of A (its rows 10-59) and 10 of B, at 1 arc-second.
    if not (rows_a in (50, 51) and rows_b in (10, 11)) or np.isnan(mosaic).any():
        fail(f"the mosaic misplaced its tiles: {rows_a} rows of A, {rows_b} of B, "
             f"{int(np.isnan(mosaic).sum())} NaN")
    print(f"  two-tile mosaic: {rows_b} rows of the northern tile, {rows_a} of the southern")

    # A plane rising northwards at 10%: Horn must read 5.71 deg, not 3.81.
    yy = np.arange(size)[::-1, None] * ry * 111320.0
    dem = np.repeat(0.1 * yy, size, axis=1)
    slope = slope_degrees(dem, lat, rx, ry)[1:-1, 1:-1]
    if not np.allclose(slope, math.degrees(math.atan(0.1)), atol=0.01):
        fail(f"slope of a 10% plane read {float(np.nanmean(slope)):.3f} deg")
    print(f"  a 10% plane reads {float(np.nanmean(slope)):.2f} deg")

    # Edges a quarter pixel off the lattice, so no centre sits on the boundary.
    padded = R.plan_grid(a, [west + 5.25 * rx, south_a + 5.25 * ry, west + 24.75 * rx,
                             south_a + 24.75 * ry], pad=1)
    inside = L.inside_aoi([west + 5.25 * rx, south_a + 5.25 * ry, west + 24.75 * rx,
                           south_a + 24.75 * ry], padded)
    if inside[0].any() or inside[-1].any() or inside[:, 0].any() or inside[:, -1].any():
        fail("the padding ring must be outside the AOI")
    # Brute force: tile pixel k is centred at k pixels from the corner, so the
    # centres inside [5.25, 24.75] are k = 6..24 on each axis -- 19 x 19. The
    # window rounds to whole pixels and may read one more; the clip may not.
    if int(inside.sum()) != 19 * 19:
        fail(f"the AOI clip kept {int(inside.sum())} pixels; 361 centres are inside")
    print(f"  the AOI clip drops the padding ring: {int(inside.sum())} of "
          f"{inside.size} pixels read are inside")


def check_geographic_lattice(tmp_dir: str):
    """Copernicus GLO-30 is pixel-is-POINT, and a lattice through zero misses it.

    A regression test for a defect that reached a thesis figure. `plan_mosaic`
    snapped geographic mosaics to multiples of the resolution counted from
    zero, on the reasoning that tile origins sit on whole degrees. They do not:
    the CENTRE of GLO-30's first pixel is at the whole degree, so the transform
    -- which records a pixel EDGE -- sits half a pixel outside it. Tile
    N06_00_E079_00 has `top = 7.00013889`, which is 7 + res/2.

    Against a lattice through zero that is a permanent half-pixel offset, and
    `int(round(...))` resolved it by banker's rounding: `round(299.5)` is 300
    and `round(-3120.5)` is -3120, so the northern tile was pasted one row low.
    Every DEM pixel moved 15 m, and mosaic row 299 -- the tile boundary -- was
    written by no tile and came back NaN. It was visible only as a faint line
    across the elevation figure.

    Two things are checked, and the second is the one that was wrong: the
    mosaic must contain no row or column that every tile skips, AND the tiles
    must sit on the lattice exactly rather than be rounded onto it.
    """
    import rasterio
    from rasterio.transform import Affine

    from core.workflows import _raster as R

    res = 1.0 / 3600.0
    half = res / 2.0
    size = 60                       # a 60-pixel stand-in for a 3600 one

    def tile(name, south, west):
        """One pixel-is-point tile: its first pixel CENTRE is on the corner."""
        path = str(Path(tmp_dir) / name)
        with rasterio.open(
                path, "w", driver="GTiff", height=size, width=size, count=1,
                dtype="float32", crs="EPSG:4326", nodata=-9999.0,
                transform=Affine(res, 0, west - half, 0, -res,
                                 south + size * res + half)) as dst:
            dst.write(np.full((size, size), south + west, dtype="float32"), 1)
        return path

    span = size * res
    south_tile = tile("s.tif", 6.0, 79.0)
    north_tile = tile("n.tif", 6.0 + span, 79.0)

    # An AOI straddling the shared edge at latitude 6.0 + span.
    edge = 6.0 + span
    bbox = [79.0 + span * 0.2, edge - span * 0.3,
            79.0 + span * 0.8, edge + span * 0.3]

    plan = R.plan_mosaic([south_tile, north_tile], bbox)

    rows, cols = set(), set()
    for href, src, win, (r0, r1, c0, c1), _ in R._placements(
            [south_tile, north_tile], plan):
        rows |= set(range(r0, r1))
        cols |= set(range(c0, c1))

    missing_rows = sorted(set(range(plan.shape[0])) - rows)
    missing_cols = sorted(set(range(plan.shape[1])) - cols)
    if missing_rows or missing_cols:
        fail(f"the mosaic has rows {missing_rows} and columns {missing_cols} "
             "that no tile writes -- a pixel-is-point lattice was rounded "
             "rather than anchored")
    print(f"  two pixel-is-point tiles -> {plan.shape}, no unwritten row")

    # And the array itself must come back whole.
    arr = R.read_band_mosaic([south_tile, north_tile], plan)
    blank = [r for r in range(arr.shape[0]) if not np.isfinite(arr[r]).any()]
    if blank:
        fail(f"rows {blank} of the mosaic are entirely NaN")
    if plan.coverage != 1.0:
        fail(f"two tiles spanning the AOI must cover it, got {plan.coverage}")
    print(f"  read back with coverage {plan.coverage}, no all-NaN row")

    # The anchor tile's own origin must be on the mosaic lattice EXACTLY --
    # this is what a lattice through zero could not deliver.
    with rasterio.open(south_tile) as src:
        steps_x = (src.transform.c - plan.transform.c) / plan.resolution
        steps_y = (plan.transform.f - src.transform.f) / plan.resolution
    if abs(steps_x - round(steps_x)) > 1e-9 or abs(steps_y - round(steps_y)) > 1e-9:
        fail(f"the anchor tile sits at ({steps_x}, {steps_y}) pixels from the "
             "mosaic origin -- not whole numbers, so pixels do not line up")
    print(f"  tile origin sits at whole pixel offsets ({steps_x:.0f}, {steps_y:.0f})")

    # A tile genuinely off the lattice must still be refused.
    off = tile("off.tif", 6.0 + span, 79.0)
    with rasterio.open(off, "r+") as dst:
        dst.transform = Affine(res, 0, 79.0 - half + res * 0.5, 0, -res,
                               dst.transform.f)
    try:
        R.plan_mosaic([south_tile, off], bbox)
        fail("a tile half a pixel off the lattice must still be refused")
    except ValueError:
        print("  a genuinely misaligned tile is still refused")


def check_mosaic_acquisition():
    """A mosaic says whether it is one observation, and the span cannot say it.

    This is a regression test for a bug that reached a stored deliverable.
    `_scene_block` decided "one datatake" by testing whether the members'
    timestamps were identical. They never are: a Sentinel-2 tile product is cut
    out of a continuous datatake and carries the time of its own cut, so two
    tiles of a single pass are seconds apart as a matter of course. The real
    Kelani pair -- both from GS2B_20251031T045839_045190_N05.11, one satellite,
    one orbit, 3.4 s between cuts -- was therefore described to the consuming
    agent as "several observations pasted together", and the agent wrote that
    into a brief, adding "on different orbits" because nothing it had been
    given said otherwise.

    The lesson the test encodes: a derived signal (a time difference) must not
    stand in for the recorded fact (the datatake id) when the archive supplies
    the fact.
    """
    from core.workflows import sentinel as S

    class _Item:
        def __init__(self, id_, tile, stamp, datatake):
            self.id = id_
            self.properties = {"datetime": stamp, "grid:code": tile,
                               "eo:cloud_cover": 5.0, "platform": "sentinel-2b",
                               "s2:processing_baseline": "05.11"}
            if datatake:
                self.properties["s2:datatake_id"] = datatake

    TAKE = "GS2B_20251031T045839_045190_N05.11"
    valid = np.ones((10, 10), dtype=bool)

    # The real case: one pass, two cuts, 3.4 s apart.
    one_pass = [
        _Item("S2B_T44NLN_20251031T050946_L2A", "MGRS-44NLN",
              "2025-10-31T05:16:55.994000Z", TAKE),
        _Item("S2B_T44NMN_20251031T050946_L2A", "MGRS-44NMN",
              "2025-10-31T05:16:52.552000Z", TAKE),
    ]
    block = S._scene_block(one_pass, valid, "2025-11-24", True)
    if block["acquisition_span_seconds"] != 3.4:
        fail(f"span should still be reported as 3.4 s, got "
             f"{block['acquisition_span_seconds']}")
    if not block["single_datatake"]:
        fail("two scenes of one datatake must be reported as a single pass, "
             "whatever their timestamps say")
    if "pasted together" in block["acquisition_note"]:
        fail("a single pass must not be described as scenes pasted together")
    if block["datatakes"] != [TAKE]:
        fail(f"the datatake should be named: {block['datatakes']}")
    print(f"  one datatake, 3.4 s between cuts -> single_datatake True")

    # `datetime` is the OLDEST member: a baseline is as recent as its stalest
    # part, not its freshest.
    if block["datetime"] != "2025-10-31T05:16:52.552000Z":
        fail(f"scene.datetime must be the oldest member, got {block['datetime']}")
    print("  scene.datetime is the oldest member, not the newest")

    # Genuinely two passes: different datatakes, and it must say so.
    two_pass = [
        _Item("A", "MGRS-44NLN", "2025-10-31T05:16:55Z", TAKE),
        _Item("B", "MGRS-44NMN", "2025-10-28T05:20:11Z", "GS2A_OTHER_N05.11"),
    ]
    block = S._scene_block(two_pass, valid, "2025-11-24", True)
    if block["single_datatake"]:
        fail("two different datatakes must not be reported as one pass")
    if "pasted together" not in block["acquisition_note"]:
        fail(f"two passes must say so: {block['acquisition_note']}")
    print("  two datatakes -> reported as several observations")

    # The catalogue omitting the field is a third state, not a guess.
    unknown = [
        _Item("A", "MGRS-44NLN", "2025-10-31T05:16:55Z", TAKE),
        _Item("B", "MGRS-44NMN", "2025-10-31T05:16:52Z", None),
    ]
    block = S._scene_block(unknown, valid, "2025-11-24", True)
    if block["single_datatake"]:
        fail("a missing datatake must not be assumed to match")
    if "cannot be established" not in block["acquisition_note"]:
        fail(f"a missing datatake must say so: {block['acquisition_note']}")
    print("  a datatake the catalogue omits -> 'cannot be established'")

    # A single scene carries none of this.
    block = S._scene_block([one_pass[0]], valid, "2025-11-24", False)
    if block["is_mosaic"] or "acquisition_note" in block:
        fail("a single scene must not carry mosaic fields")
    print("  a single scene carries no mosaic fields")


def check_land_mask(tmp_dir: str):
    """The sea comes out; lakes and rivers stay; the denominator follows.

    A rectangle is not a coastline, so any coastal AOI contains ocean, and
    ocean is not a land cover. Measured on the extended Kelani AOI: JRC
    Global Surface Water reports the sea as water occurring 100% of the time,
    so without this mask "permanent water" came to 214.47 km2 -- 13.9% of the
    AOI -- and a flood baseline meant to say how much of the catchment is
    normally wet would have been reporting the Indian Ocean. With the sea out
    it is 1.66 km2.

    Three things are checked, and the second is the one most likely to be got
    wrong by a well-meaning change: the ocean is removed, inland water is NOT,
    and every fraction moves onto the land denominator rather than the box.
    """
    import rasterio
    from rasterio.transform import Affine

    from core.workflows import _raster as R
    from core.workflows import landmask as L

    res, size = 1.0 / 3600.0, 120
    half = res / 2.0

    # A synthetic WBM tile: west half ocean, an inland lake, a river line,
    # the rest land. Pixel-is-point, like the real thing.
    codes = np.zeros((size, size), dtype="uint8")
    codes[:, : size // 2] = 1            # ocean, the western half
    codes[20:30, 70:80] = 2              # a lake
    codes[:, 90] = 3                     # a river running north-south
    path = str(Path(tmp_dir) / "wbm.tif")
    with rasterio.open(path, "w", driver="GTiff", height=size, width=size,
                       count=1, dtype="uint8", crs="EPSG:4326", nodata=None,
                       transform=Affine(res, 0, 79.0 - half, 0, -res,
                                        6.0 + size * res + half)) as dst:
        dst.write(codes, 1)

    # The AOI is the tile itself. `wbm_url` is the seam that is stubbed, NOT
    # `water_codes` -- so the mosaic and the warp both really run, which is
    # where a shape or alignment mistake would actually live.
    bbox = [79.0, 6.0, 79.0 + size * res, 6.0 + size * res]
    plan = R.plan_mosaic([path], bbox)

    def masked(exclude):
        original = L.wbm_url
        L.wbm_url = lambda lat, lon: path
        try:
            return L.land_mask(bbox, plan, exclude=exclude)
        finally:
            L.wbm_url = original

    def at(lat, lon, arr):
        """The mask value at a coordinate, not at an assumed pixel index."""
        col = int((lon - plan.transform.c) / plan.resolution)
        row = int((plan.transform.f - lat) / plan.resolution)
        return bool(arr[min(max(row, 0), arr.shape[0] - 1),
                        min(max(col, 0), arr.shape[1] - 1)])

    north = 6.0 + size * res
    lake_lat, lake_lon = north - 25 * res, 79.0 - half + 75 * res
    river_lat, river_lon = north - 60 * res, 79.0 - half + 90.5 * res
    sea_lat, sea_lon = north - 60 * res, 79.0 - half + 20 * res
    dry_lat, dry_lon = north - 60 * res, 79.0 - half + 110 * res

    keep, report = masked(("ocean",))

    # 1. The ocean is gone.
    if at(sea_lat, sea_lon, keep):
        fail("an ocean pixel survived the default exclusion")
    if not at(dry_lat, dry_lon, keep):
        fail("dry land was removed")
    print(f"  ocean removed, dry land kept "
          f"({report['land_fraction_of_aoi']:.1%} of the box is land)")

    # 2. Inland water is NOT gone. This is the one a tidy-minded change breaks.
    if not at(lake_lat, lake_lon, keep):
        fail("the lake was removed -- a lake is part of a catchment")
    if not at(river_lat, river_lon, keep):
        fail("the river was removed -- the Kelani's own channel is code 3, "
             "and deleting it would delete the river from a river-basin study")
    print("  lake and river kept, as a catchment requires")

    # 3. The denominator moved off the bounding box.
    if not math.isclose(report["land_fraction_of_aoi"], 0.5, abs_tol=0.01):
        fail(f"land fraction should be ~0.5, got {report['land_fraction_of_aoi']}")
    if report["land_area_km2"] >= report["aoi_area_km2"]:
        fail("the land area must be smaller than the bounding box area")
    if not math.isclose(report["area_km2_by_class"]["ocean"]
                        + report["land_area_km2"],
                        report["aoi_area_km2"], rel_tol=1e-6):
        fail("ocean + land must account for the whole box")
    print(f"  denominator: box {report['aoi_area_km2']:.4f} -> land "
          f"{report['land_area_km2']:.4f} km2, and they reconcile")

    # Excluding everything is allowed, and excluding nothing keeps everything.
    keep_all, _ = masked(())
    if not keep_all.all():
        fail("exclude=() must keep every pixel")
    keep_none, _ = masked(("ocean", "lake", "river"))
    if at(lake_lat, lake_lon, keep_none) or at(river_lat, river_lon, keep_none):
        fail("an explicit exclusion of lake and river must remove them")
    print("  exclude=() keeps all; naming lake and river removes them")

    # A class the mask does not have is refused rather than ignored, because a
    # typo that silently excludes nothing is a mask that is quietly not there.
    try:
        masked(("sea",))
        fail("an unknown class name must be refused, not ignored")
    except ValueError as exc:
        if "sea" not in str(exc):
            fail(f"the refusal should name the bad class: {exc}")
    print("  an unknown class name is refused, not silently ignored")


def check_index_ranges():
    """No physically valid reflectance may push an index out of its range.

    Uniform random bands are adversarial rather than realistic -- real
    reflectances are correlated, these are not -- which is the point. They
    drive SWIR16 below SWIR22 in about half the pixels, the case that sent
    NMDI to -249 on live data.
    """
    from core.workflows.sentinel import INDEX_RANGES, compute_indices

    rng = np.random.default_rng(0)
    bands = {name: rng.random((300, 300)).astype("float32")
             for name in ("red", "nir", "nir08", "swir16", "swir22", "green")}
    out = compute_indices(bands, list(INDEX_RANGES))

    for name, arr in out.items():
        low, high = INDEX_RANGES[name]
        valid = arr[np.isfinite(arr)]
        if valid.size and not (low <= valid.min() and valid.max() <= high):
            fail(f"{name} left {[low, high]}: {valid.min()}..{valid.max()}")
    print(f"  {len(out)} indices stay in range over 90000 random pixels")

    # NMDI is bounded by masking, so it could satisfy the check above by
    # masking everything. Half these pixels have SWIR16 > SWIR22 and should
    # survive; a bound achieved by emptiness is not a bound.
    kept = float(np.isfinite(out["nmdi"]).mean())
    if kept < 0.2:
        fail(f"NMDI kept only {kept:.3f} of pixels -- bounded by masking, "
             "not by construction")
    print(f"  NMDI mask is not vacuous: {kept:.3f} of pixels survive")


def check_scaling_guard():
    from core.workflows.sentinel import _asset_scaling

    class FakeAsset:
        def __init__(self, extra):
            self.extra_fields = extra

    scale, offset, nodata = _asset_scaling(FakeAsset(
        {"raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}]}))
    if (scale, offset, nodata) != (0.0001, -0.1, 0):
        fail(f"scaling not read from raster:bands: {scale}, {offset}, {nodata}")
    print("  reads scale 0.0001 / offset -0.1 from raster:bands")

    # This is the one bug class no range assertion can catch: a normalised
    # difference of raw uint16 DN is still inside [-1, 1]. It has to fail at
    # the point of reading, so missing metadata must raise, never default.
    for broken in ({}, {"raster:bands": []}, {"raster:bands": [{"nodata": 0}]}):
        try:
            _asset_scaling(FakeAsset(broken), "red")
        except ValueError:
            continue
        fail(f"missing scaling silently defaulted instead of raising: {broken}")
    print("  missing/partial raster:bands raises rather than assuming 1.0/0.0")


def check_raster_roundtrip(tmp_dir: str):
    """A written index must read back as the values that went in.

    BUI spans [-2, 2], so writing it against the default [-1, 1] range clips a
    fifth of it on disk while the in-memory statistics still report the true
    values. Nothing downstream can detect that disagreement between the JSON
    and the GeoTIFF, so it is caught here.
    """
    import rasterio
    from core.workflows import _raster as R

    plan = R.GridPlan(None, rasterio.Affine(20.0, 0, 300000, 0, -20.0, 2900040),
                      rasterio.CRS.from_epsg(32646), (2, 4), 20.0)
    values = np.array([[-1.9, -1.0, 0.0, 1.9],
                       [-0.5, np.nan, 0.5, 1.234]], dtype="float32")

    path = os.path.join(tmp_dir, "bui_roundtrip.tif")
    meta = R.write_index(path, values, plan, value_range=(-2.0, 2.0))

    with rasterio.open(path) as src:
        raw = src.read(1)
    back = np.where(raw == R.INDEX_NODATA, np.nan,
                    raw * meta["scale"]).astype("float32")

    finite = np.isfinite(values)
    if not np.allclose(back[finite], values[finite], atol=1e-4):
        fail(f"round trip changed values:\n  in  {values[finite]}\n"
             f"  out {back[finite]}")
    if np.isfinite(back[1, 1]):
        fail("NaN did not survive as nodata")
    print(f"  [-2, 2] round trip exact to 1e-4, NaN -> {R.INDEX_NODATA}")

    # And the writer must refuse a range int16 cannot hold rather than clip it.
    try:
        R.write_index(os.path.join(tmp_dir, "overflow.tif"), values, plan,
                      value_range=(-5.0, 5.0))
    except ValueError:
        print("  a range overflowing int16 raises rather than clipping")
    else:
        fail("write_index accepted a range that overflows int16")


# ---------------------------------------------------------------------------
# 6. live
# ---------------------------------------------------------------------------
def check_live_water():
    from core.workflows.water import fetch_permanent_water

    out = fetch_permanent_water(bbox=BBOX, write_rasters=False)
    w = out["water"]
    total = sum(w[k]["pixels"] for k in
                ("permanent", "seasonal", "ephemeral", "never_water",
                 "unobserved"))
    if total != out["grid"]["shape"][0] * out["grid"]["shape"][1]:
        fail(f"water bands do not partition the AOI: {total} pixels accounted")
    print(f"  AOI {out['aoi']['area_km2']} km^2 from JRC GSW")
    print(f"  permanent {w['permanent']['area_km2']} km^2, "
          f"seasonal {w['seasonal']['area_km2']}, "
          f"ephemeral {w['ephemeral']['area_km2']}")
    print("  bands partition the AOI exactly")
    return out


def check_live_terrain(water):
    from core.workflows.terrain import fetch_terrain_data

    out = fetch_terrain_data(bbox=BBOX, write_rasters=False)
    s, e = out["slope"]["stats"], out["elevation"]["stats"]

    if not 0 <= s["min"] <= s["max"] <= 90:
        fail(f"slope outside 0-90 degrees: {s['min']}..{s['max']}")
    if not 500 < e["mean"] < 2500:
        fail(f"Shillong plateau mean elevation {e['mean']} m is implausible")
    print(f"  elevation {e['min']}..{e['max']} m, mean {e['mean']}")
    print(f"  slope mean {s['mean']} deg, p90 {s['p90']}, "
          f"valid_fraction {s['coverage']['valid_fraction']}")
    print(f"  classes: {out['slope']['classes']['fractions']}")

    # Two independent datasets, two grids, one AOI. They should agree closely
    # on its area; a large gap means a windowing bug in one of them.
    dem_area = sum(out["slope"]["classes"]["area_km2"].values())
    gsw_area = water["aoi"]["area_km2"]
    if abs(dem_area - gsw_area) / gsw_area > 0.05:
        fail(f"AOI area disagrees: DEM {dem_area} vs GSW {gsw_area} km^2")
    print(f"  cross-check: DEM {dem_area:.1f} km^2 vs GSW {gsw_area:.1f} km^2")


def check_no_relaxed_live_calls():
    """No test calls fetch_lulc_data with a threshold of its own (BUG-003).

    The failure-record tests that build a relaxed or stricter FAILURE on
    purpose (test_fact_sheet, test_worldcover) pass the threshold to a
    helper, not to fetch_lulc_data, and are not matched."""
    import re

    pattern = re.compile(r"fetch_lulc_data\((?:[^()]|\([^()]*\))*?"
                         r"\b(min_valid_fraction|lookback_days|max_probes)\s*=", re.S)
    offenders = []
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {m.group(1)}")
    if offenders:
        fail("a test calls fetch_lulc_data with a non-default threshold: "
             + "; ".join(offenders))
    print("  no test overrides fetch_lulc_data's thresholds")


def check_step_down():
    """The step-down is OFF: 0.7 is the one threshold (user decision
    2026-11-01). The `step_down` helper still steps through a list it is
    given -- only on a cloud failure, every attempt kept with its threshold, a
    tiling failure raised at once -- and fetch_lulc_data gives it none."""
    from core.workflows import sentinel as S

    if S.MIN_VALID_FRACTION != 0.7 or tuple(S.STEP_DOWN_FRACTIONS) != ():
        fail(f"threshold is {S.MIN_VALID_FRACTION} with step-down "
             f"{S.STEP_DOWN_FRACTIONS}; it is fixed at 0.7 with none")
    defaults = {k: v.default for k, v in
                inspect.signature(S.fetch_lulc_data).parameters.items()}
    if S.LOOKBACK_DAYS != 90 or defaults["lookback_days"] != 90:
        fail(f"lookback is {S.LOOKBACK_DAYS} / {defaults['lookback_days']}; it is fixed at 90")
    if defaults["min_valid_fraction"] != 0.7 or list(defaults["step_down_to"]) != []:
        fail(f"fetch_lulc_data defaults: {defaults['min_valid_fraction']}, "
             f"{defaults['step_down_to']}")
    print("  0.7 fixed, no step-down, in the constants and the tool's defaults")
    LADDER = (0.6, 0.5)            # the helper on its own, given a list

    def cloudy_until(ok_at):
        calls = []

        def select(t):
            calls.append(t)
            if t > ok_at:
                raise S.BelowValidFraction("too cloudy", [{"date": f"d{t}"}],
                                           min_valid_fraction=t, best={"date": f"d{t}"})
            return (["scene"], "plan", "scl", [{"date": "ok", "accepted": True}], False)
        return select, calls

    select, calls = cloudy_until(0.6)
    (members, _, _, attempts, _), used, tried = S.step_down(select, 0.7, LADDER)
    if not (used == 0.6 and tried == [0.7, 0.6] and calls == [0.7, 0.6]
            and [a["threshold"] for a in attempts] == [0.7, 0.6]):
        fail(f"step-down to 0.6 went wrong: used {used}, tried {tried}, attempts {attempts}")
    print("  0.7 fails on cloud -> 0.6 accepted; both attempts kept with their threshold")

    select, calls = cloudy_until(0.0)
    try:
        S.step_down(select, 0.7, LADDER)
        fail("all three thresholds failed and nothing was raised")
    except S.BelowValidFraction as exc:
        if calls != [0.7, 0.6, 0.5] or len(exc.attempts) != 3 or exc.min_valid_fraction != 0.5:
            fail(f"the last failure lost its record: {calls}, {exc.attempts}")
    print("  0.7, 0.6, 0.5 all fail -> BelowValidFraction with every attempt (the fallback's cue)")

    calls = []

    def tiling(t):
        calls.append(t)
        raise ValueError("tiles are not one grid")
    try:
        S.step_down(tiling, 0.7, LADDER)
        fail("a tiling failure was not raised")
    except S.BelowValidFraction:
        fail("a tiling failure was treated as cloud")
    except ValueError:
        if calls != [0.7]:
            fail(f"a tiling failure stepped the threshold down: {calls}")
    print("  a tiling failure is raised at once; the threshold does not step down")

    select, calls = cloudy_until(0.0)
    try:
        S.step_down(select, 0.7, [])
    except S.BelowValidFraction:
        pass
    if calls != [0.7]:
        fail(f"step_down_to=[] still stepped down: {calls}")
    print("  step_down_to=[] keeps the one threshold")


def check_live_sentinel():
    from core.workflows.sentinel import fetch_lulc_data

    import inspect

    # At the defaults (BUG-003): nothing is passed but the place and the date.
    out = fetch_lulc_data(bbox=S2_BBOX, start_date=S2_START, write_rasters=False)
    scene, sel = out["scene"], out["selection"]
    default = inspect.signature(fetch_lulc_data).parameters["min_valid_fraction"].default

    print(f"  selected {scene['id']} ({scene['datetime'][:10]}), "
          f"{scene['days_before_start']} days before {S2_START}")
    print(f"  tile cloud {scene['scene_cloud_cover']:.1f}%, "
          f"AOI valid {scene['aoi_valid_fraction']}")
    print(f"  probed {sel['scenes_probed']} of {sel['scenes_found']} scenes")

    if scene["days_before_start"] < 0:
        fail("selected a scene AFTER start_date -- the baseline must be prior")
    if default != 0.7:
        fail(f"fetch_lulc_data's default min_valid_fraction is {default}, not 0.7")
    if scene["aoi_valid_fraction"] < default:
        fail(f"accepted a scene below the default threshold {default}: {scene}")

    # The point of the SCL probe: tile cloud cover does not predict AOI clarity.
    # If it did, this whole mechanism would be redundant with a catalogue sort.
    probed = [a for a in sel["attempts"] if "aoi_valid_fraction" in a]
    by_cloud = sorted(probed, key=lambda a: a["scene_cloud_cover"] or 0)
    by_valid = sorted(probed, key=lambda a: -a["aoi_valid_fraction"])
    if len(probed) > 3 and [a["scene_id"] for a in by_cloud] == \
            [a["scene_id"] for a in by_valid]:
        print("  NOTE: tile cloud ranking matched AOI ranking on this run")
    else:
        worst = min(probed, key=lambda a: a["aoi_valid_fraction"])
        print(f"  tile cloud is a poor proxy: {worst['scene_cloud_cover']:.1f}% "
              f"cloud gave {worst['aoi_valid_fraction']} AOI valid")

    for name, entry in out["indices"].items():
        st = entry["stats"]
        lo, hi = entry["range"]
        if st["min"] is None:
            fail(f"{name} produced no valid pixels")
        if not lo <= st["min"] <= st["max"] <= hi:
            fail(f"{name} outside {entry['range']}: {st['min']}..{st['max']}")
        print(f"  {name:<6} mean {st['mean']:<9} range [{st['min']}, {st['max']}]")

    if "bui" in out["indices"] and out["indices"]["bui"]["range"] != [-2.0, 2.0]:
        fail("BUI must be summarised over [-2, 2], not [-1, 1]")

    # NMDI's bound comes from masking, so on real data too it must keep a fair
    # share of what NDVI keeps -- otherwise it is in range only by being empty.
    ndvi_valid = out["indices"]["ndvi"]["stats"]["coverage"]["valid_fraction"]
    nmdi_valid = out["indices"]["nmdi"]["stats"]["coverage"]["valid_fraction"]
    if nmdi_valid < 0.5 * ndvi_valid:
        fail(f"NMDI kept {nmdi_valid} against NDVI's {ndvi_valid} -- the mask "
             "is doing too much work")
    print(f"  NMDI valid {nmdi_valid} vs NDVI {ndvi_valid}")

    fractions = out["indices"]["ndvi"]["classes"]["fractions"]
    if not math.isclose(sum(fractions.values()), 1.0, abs_tol=0.01):
        fail(f"NDVI class fractions should sum to 1.0, got {sum(fractions.values())}")
    print(f"  NDVI classes sum to 1.0: {fractions}")


# ---------------------------------------------------------------------------
def main() -> int:
    offline = "--offline" in sys.argv

    hr("1. registry")
    check_registry()

    hr("2. tile naming across hemispheres")
    check_tiling()

    hr("3. slope against a known answer")
    check_slope()

    hr("4. indices")
    check_indices()

    hr("5. grid and statistics guards")
    check_grid_guard()

    hr("6. index ranges over random reflectance")
    check_index_ranges()

    hr("7. reflectance scaling guard")
    check_scaling_guard()

    hr("8. raster round trip")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_raster_roundtrip(tmp_dir)

    hr("8b. partial AOI coverage guard")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_aoi_coverage(tmp_dir)

    hr("8c. grid reuse across scenes of one tile")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_grid_reuse(tmp_dir)

    hr("8d. probe decimation")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_probe_decimation(tmp_dir)

    hr("8e. mosaicking across projected (MGRS) tiles")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_utm_mosaic(tmp_dir)

    hr("8f. a mosaic's acquisition verdict")
    check_mosaic_acquisition()

    hr("8g. a pixel-is-point geographic lattice")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_geographic_lattice(tmp_dir)

    hr("8g2. non-square pixels north of 50 deg (BUG-018)")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_nonsquare_lattice(tmp_dir)

    hr("8h. excluding the sea from the reporting domain")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_land_mask(tmp_dir)

    hr("8i. scene selection judges the land, not the sea")
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_land_denominator(tmp_dir)

    hr("8j. no test lowers fetch_lulc_data's thresholds (BUG-003)")
    check_no_relaxed_live_calls()

    hr("8k. the valid-fraction threshold steps down 0.7 -> 0.6 -> 0.5 on cloud only")
    check_step_down()

    if offline:
        hr("SKIPPED: live catalogue calls (--offline)")
        print("  Rerun without --offline to hit Earth Search, JRC and AWS.")
    else:
        hr("9a. live: JRC Global Surface Water")
        water = check_live_water()

        hr("9b. live: Copernicus DEM GLO-30")
        check_live_terrain(water)

        hr("9c. live: Sentinel-2 scene selection")
        check_live_sentinel()

    print(f"\n{'=' * 68}\nALL CHECKS PASSED\n{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
