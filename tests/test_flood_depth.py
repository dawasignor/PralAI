"""Flood depth (calc_flood_depth, FwDET v2.1) and road passability (BUG-012).

    python tests/test_flood_depth.py      # no model, no network, no cost

On synthetic DEMs with a known answer:

    1. fwdet      -- on a ramp the depth is the boundary elevation minus the
                     ground, exactly; smoothing a level boundary changes nothing
    2. filters    -- the coastal filter drops boundary cells at or next to 0 m;
                     the slope filter drops those BELOW the threshold (the
                     paper's direction); unobserved cells and permanent water
                     are never shoreline; a negative depth is 0 and counted
    3. error      -- seeded and repeatable; a pixel deep inside a class keeps
                     it, a pixel at a class edge does not; the boundary shifts
                     touch observed dry land only
    4. tool       -- the payload, its rasters, its steps for Entro, and the
                     person bands NOT KNOWN until sourced
    5. roads      -- flooded road km by Kramer class from the depth raster
    6. bias       -- depth on GLO-30 is labelled biased high (BUG-014) in the
                     payload and beside every depth figure of the reports, with
                     the edge-above-ground evidence and no claimed size
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import logbook, uncertainty                  # noqa: E402
from core.workflows import _raster as R, depth, impact  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
RES = 1 / 3600                       # GLO-30: 1 arc-second
LAT = 6.94
BBOX = [79.94, 6.93, 79.94 + 60 * RES, 6.93 + 40 * RES]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def ramp(rows: int = 40, cols: int = 60, rise: float = 1.0, base: float = 10.0) -> np.ndarray:
    """Ground rising `rise` m per pixel to the east (about 3 % at 30 m)."""
    return (base + rise * np.arange(cols, dtype=float))[None, :].repeat(rows, axis=0)


def codes_west(rows: int = 40, cols: int = 60, edge: int = 20) -> np.ndarray:
    c = np.zeros((rows, cols), dtype="uint8")
    c[:, :edge] = depth.FLOODED
    return c


def test_fwdet() -> None:
    dem, codes = ramp(), codes_west()
    run = depth.fwdet(dem, codes, latitude=LAT, resolution_deg=RES,
                      smoothing_iterations=0, slope_threshold_pct=0.5)
    want = np.where(codes == 1, 30.0 - dem, np.nan)
    check("on a ramp, depth = boundary elevation - ground, exactly",
          np.allclose(run["depth"], want, equal_nan=True), f"{np.nanmax(run['depth'])}")
    check("the boundary is the column of dry cells beside the flood",
          run["counts"]["cells_initial"] == 40 and run["counts"]["cells_used"] == 40)
    smooth = depth.fwdet(dem, codes, latitude=LAT, resolution_deg=RES,
                         smoothing_iterations=10, slope_threshold_pct=0.5)
    check("smoothing a level boundary changes nothing",
          np.allclose(smooth["depth"], want, equal_nan=True))


def test_filters() -> None:
    dem, codes = ramp(), codes_west()
    low = dem.copy()
    low[:10, :] -= 40.0                                 # rows 0-9 sit below 0 m
    run = depth.fwdet(low, codes, latitude=LAT, resolution_deg=RES,
                      smoothing_iterations=0, slope_threshold_pct=0.0)
    check("coastal filter: boundary cells at or next to 0 m are dropped",
          run["counts"]["removed_coastal"] == 11, str(run["counts"]))
    flat = dem.copy()
    flat[20:, :] = 30.0                                  # the south half is level ground
    run = depth.fwdet(flat, codes, latitude=LAT, resolution_deg=RES,
                      smoothing_iterations=0, slope_threshold_pct=0.5)
    # Rows 21-38 are measured flat; row 20 sees the ramp beside it, and row
    # 39 (the grid's edge) has no computable slope, which is not "flat".
    check("slope filter: boundary cells BELOW the threshold are dropped, an unknown slope is not",
          run["counts"]["removed_slope"] == 18 and run["counts"]["cells_used"] == 22, str(run["counts"]))
    run0 = depth.fwdet(flat, codes, latitude=LAT, resolution_deg=RES,
                       smoothing_iterations=0, slope_threshold_pct=0.0)
    check("a zero threshold keeps every boundary cell", run0["counts"]["removed_slope"] == 0)

    c = codes.copy()
    c[:, 20] = depth.UNOBSERVED
    c[:20, 21] = depth.PERMANENT
    run = depth.fwdet(dem, c, latitude=LAT, resolution_deg=RES,
                      smoothing_iterations=0, slope_threshold_pct=0.0)
    check("unobserved cells and permanent water are never shoreline",
          run["depth"] is None and run["counts"]["cells_initial"] == 0, str(run["counts"]))

    bump = dem.copy()
    bump[5, 5] = 100.0                                   # a flooded cell above the water
    run = depth.fwdet(bump, codes, latitude=LAT, resolution_deg=RES,
                      smoothing_iterations=0, slope_threshold_pct=0.0)
    check("a negative depth is 0 m, and its share is counted",
          run["depth"][5, 5] == 0.0 and abs(run["negative_share"] - 1 / 800) < 1e-12)


def test_error() -> None:
    dem, codes = ramp(), codes_west()
    kw = dict(latitude=LAT, resolution_deg=RES, realisations=6, seed=7,
              smoothing_iterations=0, slope_threshold_pct=0.0)
    a = depth.error_realisations(dem, codes, **kw)
    b = depth.error_realisations(dem, codes, **kw)
    check("the realisations are seeded and repeatable", np.array_equal(a["cube"], b["cube"], equal_nan=True))
    check("3K runs: K DEM perturbations x three boundaries", a["cube"].shape[0] == 18)
    est = depth.fwdet(dem, codes, latitude=LAT, resolution_deg=RES,
                      smoothing_iterations=0, slope_threshold_pct=0.0)["depth"]
    prob = depth.change_probability(est, a["cube"], [0.3, 0.6])
    # Column 0 is 20 m deep; column 19 is 1 m deep beside the edge (eroded -> 0 m).
    check("deep inside a class the band holds; at the flood edge it moves",
          np.nanmax(prob[:, :5]) == 0.0 and np.nanmean(prob[:, 19]) > 0.0,
          f"{np.nanmax(prob[:, :5])}, {np.nanmean(prob[:, 19]):.2f}")
    c = codes.copy()
    c[:, 25:] = depth.UNOBSERVED
    grown = depth._shift(c, "dilated 1 px")
    check("a boundary shift grows into observed dry land, never into unobserved",
          (grown[:, 20] == 1).all() and (grown[:, 25:] == depth.UNOBSERVED).all())
    shrunk = depth._shift(codes, "eroded 1 px")
    check("an eroded boundary gives the edge back to dry land", (shrunk[1:-1, 19] == depth.DRY).all())
    sig = depth.dem_sigma(np.array([5.0, 25.0]))
    check("DEM sigma = relative LE90 / 1.645: 2 m to 20 % slope, 4 m above",
          np.allclose(sig, [2.0 / 1.6448536269514722, 4.0 / 1.6448536269514722]))


def _plan():
    from rasterio.transform import from_origin

    t = from_origin(BBOX[0], BBOX[3], RES, RES)
    return R.GridPlan(None, t, "EPSG:4326", (40, 60), RES, pixel_area_km2=(RES * 111320) ** 2 *
                      np.cos(np.radians(LAT)) / 1e6, units="deg")


def _write_codes(path: Path, codes: np.ndarray, plan) -> str:
    import rasterio

    with rasterio.open(path, "w", driver="GTiff", height=codes.shape[0], width=codes.shape[1],
                       count=1, dtype="uint8", crs=plan.crs, transform=plan.transform,
                       nodata=255) as dst:
        dst.write(codes, 1)
    return str(path)


def test_tool(root: Path) -> dict:
    plan = _plan()
    dem = ramp()
    extent = _write_codes(root / "flood_extent_test.tif", codes_west(), plan)
    saved = depth._read_dem
    depth._read_dem = lambda bbox: (dem, plan, ["synthetic"])
    try:
        out = depth.calc_flood_depth(BBOX, extent, output_dir=str(root), event_id="e",
                                     smoothing_iterations=0, slope_threshold_pct=0.5,
                                     realisations=3, seed=1)
    finally:
        depth._read_dem = saved
    check("the tool's depth matches the closed form on its own grid",
          abs(out["depth"]["max_m"] - 20.0) < 1e-4 and out["flooded"]["cells"] == 800)
    names = {"depth", "depth_median", "depth_p05", "depth_p95", "depth_sd",
             "band_change_prob_vehicle", "band_change_prob_advisory"}
    have = {"depth"} | set(out["error"]["rasters"])
    check("the depth, its spread and the band-change rasters are written",
          have == names and all(Path(v["path"]).exists() for v in out["error"]["rasters"].values()))
    shares = {c["class"]: c["share"] for c in out["vehicle_classes"]}
    check("vehicle classes: on a 1-20 m ramp everything is impassable",
          shares == {"passable by car": 0.0, "emergency vehicles only": 0.0, "impassable": 1.0})
    check("the person bands are NOT KNOWN until their sources are read",
          out["person_bands"]["status"] == "not_known"
          and any(i["key"] == "person_bands_pending" for i in out["uncertainty"]["tool"]))
    steps, block = uncertainty.capture("calc_flood_depth", out, {})
    check("Entro gets the depth error step by step, each stage with its items",
          [s["step"] for s in steps][:3] == ["read the DEM", "extent onto the DEM grid", "boundary cells"]
          and len(steps) == 7 and any(i["key"] == "error_model" for s in steps for i in s["uncertainty"]))
    check("the paper's parameter choice is labelled assumed for GLO-30",
          next(i for i in out["uncertainty"]["tool"] if i["key"] == "fwdet_parameters")["basis"] == "assumed")
    return out


def test_roads(root: Path, out: dict) -> None:
    plan = _plan()
    extent = str(root / "flood_extent_test.tif")
    depth_path = out["depth"]["raster"]["path"]
    lat = BBOX[1] + 20.5 * RES
    # One road along the row, west to east across the flood and on to dry land.
    road = {"id": 1, "name": "Test Road", "class": "primary", "bridge": False,
            "coords": [(BBOX[0] + 0.5 * RES, lat), (BBOX[0] + 30.5 * RES, lat)]}
    saved = impact._roads_and_hospitals
    impact._roads_and_hospitals = lambda bbox: ([road], [])
    try:
        r = impact.assess_road_access(BBOX, extent, depth_raster=depth_path,
                                      band_change_raster=out["error"]["rasters"]["band_change_prob_vehicle"]["path"])
        r0 = impact.assess_road_access(BBOX, extent)
    finally:
        impact._roads_and_hospitals = saved
    km = r["passability_km"]
    check("with depth, flooded road is classed by Kramer depth (all impassable here)",
          km["impassable"] > 0 and km["passable by car"] == 0 and km["emergency vehicles only"] == 0
          and abs(km["impassable"] - r["roads_km_total"]["flooded_km"]) < 1e-6, str(km))
    check("without depth, the road state says depth not computed",
          r0["passability_km"] is None and r0["road_state"] == impact.DEPTH_NOT_COMPUTED)
    check("the band-change share of flooded road is reported", r["passability_unstable_km"] is not None)


def test_bias_labels(root: Path, out: dict) -> None:
    from core import report_render
    from core.experiment import Experiment

    check("the payload says biased high, with its evidence and no size",
          out["bias"]["label"] == "biased high" and out["bias"]["magnitude"] == "not measured"
          and out["bias"]["evidence_edge_above_flood_ground_m"] is not None
          and next(i for i in out["uncertainty"]["tool"] if i["key"] == "depth_bias")["basis"] == "assumed")
    run = Experiment("bias-test", root=root / "exp")
    run.create(place="p", window="2025-11-24 to 2025-12-01")
    run.cache_input("rank_flood_products", {"ranking": [{"id": "test", "alternative": "Test", "rank": 1}]})
    run.cache_input("get_flood_extent", {"product": "test", "label": "Test", "status": "flood_mapped",
                                         "flood_km2": 1.0, "coverage": 1.0,
                                         "raster": {"path": out["extent_raster"]}})
    run.cache_input("calc_flood_depth", out)
    run.cache_input("assess_road_access", {"roads_km_total": {"flooded_km": 1.0},
                                           "passability_km": {"impassable": 1.0}, "hospitals": []})
    ctx = report_render.Ctx(run)
    tile, bands = report_render.f_flood_depth(ctx), report_render.f_depth_bands(ctx)
    unc, roads = report_render.f_depth_uncertainty(ctx), report_render.f_roads_passability(ctx)
    check("the depth tile reads 'biased high' beside the number",
          "biased high" in tile.unit and tile.basis == "assumed", tile.unit)
    check("the band table, the uncertainty line and road passability say so too",
          "biased high" in bands.text and unc.text.startswith("Depth is biased high")
          and "not measured" in unc.text and "biased high" in roads.text)
    # Depth is switched off and left the Module 9 layout on 2026-11-02; its
    # blocks still say the bias wherever a template places them.
    m9 = report_render.render(run, "essential_services_hev",
                              template_text="{{fact:depth_summary}}\n\n{{fact:depth_bias}}"
                              ).read_text(encoding="utf-8")
    check("the depth blocks state the bias beside the median and under the table",
          "(**biased high**)" in m9 and "Every depth here is biased high" in m9)


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        test_fwdet()
        test_filters()
        test_error()
        with tempfile.TemporaryDirectory() as raw:
            out = test_tool(Path(raw))
            test_roads(Path(raw), out)
            test_bias_labels(Path(raw), out)
    finally:
        logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
