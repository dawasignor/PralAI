"""The Flood analyst outputs (Modules 1-10): rendered from what is stored
into their live vault templates, gaps said aloud.

    python tests/test_outputs.py            # no model, no network, no cost

Reads the templates from the vault (VAULT_ROOT), as a run
does. Held still here, on a synthetic store:

    0. templates   -- every module has a vault template whose placeholders the
                      renderer knows; the template drives the layout; an empty
                      block leaves no gap; a facts sidecar is written
    1. registry    -- a version-1 experiment is refused, its files untouched
    2. gaps        -- a missing input renders "Not known yet" and names the tool
    3. window      -- Output 1 warns when the check was fetched for another window
    4. how sure    -- the four levels come from the payloads' own blocks; a
                      payload from before the contract is listed, not invented
    5. land cover  -- Output 3 uses WorldCover only with a recorded Sentinel-2
                      failure at the defaults, and says which
    6. HEV         -- Output 9 takes the best-ranked product's extent; no flood
                      is a finding; facilities are tested against the extent;
                      a missing vault note writes no evidence
    7. health      -- Output 10 states the climate zone and its basis
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import logbook, module_blocks, report_render   # noqa: E402
from core.experiment import Experiment                    # noqa: E402
from core.report_render import Ctx, render                # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def store(root: Path, slug: str = "render-test", window: str = "2025-11-24 to 2025-12-01") -> Experiment:
    run = Experiment(slug, root=root)
    run.create(place="Test AOI", window=window)
    return run


def availability(start: str, end: str) -> dict:
    days = [f"2025-11-{d:02d}" for d in range(int(start[-2:]), 31)] + ["2025-12-01"]
    days = [d for d in days if start <= d <= end]
    prod = lambda pid, st: {"id": pid, "label": pid.upper(), "totals": {},  # noqa: E731
                            "days": {d: {"status": st, "count": 1 if st == "available" else 0}
                                     for d in days}}
    return {"period": {"start": start, "end": end, "days": days, "timezone": "Asia/Colombo",
                       "utc_offset_h": 5.5},
            "instance_definition": "one pass", "retrieved_at": "t",
            "products": [prod("gfm", "available"), prod("mcdwd", "none")],
            "uncertainty": {"input": [{"statement": "Catalogue search, not the archive.",
                                       "basis": "stated_by_source"}],
                            "tool": [], "pipeline": [], "decision": []}}


def test_templates(root: Path) -> None:
    from core.module_blocks import MODULES

    known = set(report_render.registry())
    bad = {}
    for slot in MODULES:
        text = report_render.template(slot)
        # Agent slots (`llm:`) are written by an agent, not known to the registry.
        names = {m.group(2) for m in report_render.PLACEHOLDER.finditer(text)
                 if m.group(1) != "llm"}
        if not names or names - known:
            bad[slot] = sorted(names - known) or "no placeholders"
    check("every module has a live vault template, and every placeholder in it is known",
          not bad, str(bad))
    run = store(root, "template-test")
    run.cache_input("terrain", {"aoi": {"land_area_km2": 12.5}, "slope": {"method": "Horn"},
                                "elevation": {"stats": {"min": 1.0}}})
    path = render(run, "terrain", template_text="## Custom heading\n\n{{fact:terrain_summary}}\n\n"
                                                "{{fact:slope_class_figure}}\n\nFixed line.")
    text = path.read_text(encoding="utf-8")
    check("the template drives the layout: its heading and fixed text are written",
          "## Custom heading" in text and "Fixed line." in text
          and "over 12.5 km2 of land" in text)
    check("an empty block leaves no gap",
          "of land. Slope by Horn." + chr(10) * 2 + "Fixed line." in text, repr(text[-80:]))
    check("a module writes its facts sidecar too",
          path.with_suffix(".facts.json").exists())


def test_registry_and_gaps(root: Path) -> None:
    old = Experiment("legacy-test", root=root)
    old.dir.mkdir(parents=True)
    (old.dir / "event.md").write_text("---\ntype: experiment-memory\n---\n", encoding="utf-8")
    (old.dir / "01_weather_data.md").write_text("kept as written", encoding="utf-8")
    try:
        render(old, "weather")
        check("a version-1 experiment is refused", False)
    except ValueError:
        check("a version-1 experiment is refused, its file untouched",
              (old.dir / "01_weather_data.md").read_text(encoding="utf-8") == "kept as written")
    run = store(root)
    path = render(run, "terrain")
    text = path.read_text(encoding="utf-8")
    side = __import__("json").loads(path.with_suffix(".facts.json").read_text(encoding="utf-8"))
    summary = next(f for f in side["facts"] if f["key"] == "terrain_summary")
    check("a missing input renders Not known yet, in plain words (BUG-058)",
          "Not known yet" in text and "terrain step has not run" in text
          and "fetch_terrain_data" not in text, text[-200:])
    check("... and the sidecar keeps the tool that would produce it",
          "fetch_terrain_data" in summary["reason"] and summary["run"] == ["fetch_terrain_data"],
          str(summary)[:160])
    check("the output carries registry version 2 in its frontmatter",
          "registry_version: 2" in text and "source: core/report_render.py" in text)
    check("the heading is 'Module N: title', with no source when nothing is stored",
          "# Module 4: Terrain and slope classes" + chr(10) in text)


def test_window_and_how_sure(root: Path) -> None:
    run = store(root, "window-test")
    run.cache_input("data_availability", availability("2025-11-21", "2025-12-01"))
    text = render(run, "data_availability").read_text(encoding="utf-8")
    check("Output 1 warns when the check covers another window",
          "the experiment's window is 2025-11-24 to 2025-12-01" in text)
    check("the inputs used are recorded", "inputs_used: [data_availability]" in text)
    check("the heading names the source read from the stored result",
          "# Module 1: Data availability from the GFM, MCDWD catalogues" in text)
    check("How sure comes from the payload's own block, by level",
          "## How sure are we?" in text and "**Input data**" in text
          and "Catalogue search, not the archive." in text)
    run.cache_input("data_availability", availability("2025-11-24", "2025-12-01"))
    text = render(run, "data_availability").read_text(encoding="utf-8")
    check("no warning when the windows agree", "fetched for another window" not in text)
    ctx = Ctx(run)
    ctx.rest("terrain", {"slope": {}})
    legacy = module_blocks.b_how_sure(ctx).text
    check("a payload from before the contract is listed, not given a block",
          "before the tool contract" in legacy and "`terrain`" in legacy)


def _worldcover() -> dict:
    return {"aoi": {"bbox": [79.94, 6.905, 80.09, 6.978], "land_area_km2": 134.7},
            "classes": {"fractions": {"tree_cover": 0.77, "built_up": 0.15, "cropland": 0.08},
                        "area_km2": {"tree_cover": 103.4}},
            "coverage": {"valid_fraction": 1.0},
            "accuracy": {"per_class": {"tree_cover": {"users_pct": 80.0, "producers_pct": 91.9}}},
            "source": {"time_start": "2021-01-01T00:00:00Z"},
            "uncertainty": {"input": [{"statement": "A 2021 snapshot.", "basis": "stated_by_source"}],
                            "tool": [], "pipeline": [], "decision": []}}


def test_land_cover(root: Path) -> None:
    run = store(root, "lulc-test")
    run.cache_input("worldcover", _worldcover())
    text = render(run, "land_use_pre_event").read_text(encoding="utf-8")
    check("without a recorded failure WorldCover is shown, not used",
          "(not usable as the fallback)" in text and "does not use it" in text)
    failure = {"error_type": "runtime_error", "exception": "BelowValidFraction",
               "message": "BelowValidFraction: no Sentinel-2 scene reaches valid fraction 0.7"}
    run.record_failure("lulc", failure, params={"bbox": [79.94, 6.905, 80.09, 6.978]})
    text = render(run, "land_use_pre_event").read_text(encoding="utf-8")
    check("with the failure on record it is the fallback, and the failure is quoted",
          "WorldCover 2021 (fallback)" in text and "Sentinel-2 failed" in text
          and "stepped down to 0.5" in text)
    check("the brief's absence is said, not filled", "land cover brief has not been written" in text)


def _extent_raster(path: Path, bbox: list[float]) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    arr = np.zeros((100, 100), dtype="uint8")
    arr[:, :50] = 1                     # western half flooded
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1,
                       dtype="uint8", crs="EPSG:4326", nodata=255,
                       transform=from_bounds(*bbox, 100, 100)) as dst:
        dst.write(arr, 1)


def test_hev(root: Path) -> None:
    bbox = [79.94, 6.905, 80.09, 6.978]
    run = store(root, "kelani-lower-2025")
    text = render(run, "essential_services_hev").read_text(encoding="utf-8")
    check("no ranking: the flood extent is not known, and says whose it would be",
          "the ranking is not stored" in text and "best-ranked" in text)
    run.cache_input("rank_flood_products", {"ranking": [
        {"id": "gfm", "alternative": "GFM (Sentinel-1)", "rank": 1},
        {"id": "dswx_s1", "alternative": "OPERA DSWx-S1", "rank": 2}]})
    run.cache_input("get_flood_extent", {"product": "gfm", "label": "GFM (Sentinel-1)",
                                         "status": "no_flood_mapped", "flood_km2": 0.0,
                                         "coverage": 0.14, "window": ["2025-11-24", "2025-12-01"]})
    text = render(run, "essential_services_hev").read_text(encoding="utf-8")
    check("no flood mapped is a finding, and every flood-dependent figure is NOT KNOWN",
          "maps **no flood**" in text and "a finding, not a failure" in text)

    raster = root / "extent.tif"
    _extent_raster(raster, bbox)
    run.cache_input("get_flood_extent", {"product": "gfm", "label": "GFM (Sentinel-1)",
                                         "status": "flood_mapped", "flood_km2": 60.0,
                                         "observed_km2": 120.0, "coverage": 0.9,
                                         "window": ["2025-11-24", "2025-12-01"],
                                         "raster": {"path": str(raster)}})
    text = render(run, "essential_services_hev").read_text(encoding="utf-8")
    health = render(run, "human_health_risks_hev").read_text(encoding="utf-8")
    check("the best-ranked product's extent is used", "**60.00 km2 flooded**" in text)
    check("the facilities wait for the hazard-and-exposure map, said plainly",
          "hazard-and-exposure map is not drawn yet" in text)
    check("Output 10 states the climate zone and that it is a config value",
          "tropical -- per-experiment config value" in health)
    other = store(root, "zone-test")
    zone = render(other, "human_health_risks_hev").read_text(encoding="utf-8")
    check("an experiment with no climate zone lists no climate-specific risk",
          "no climate zone is set" in zone)

    # The map's counts, for THIS extent, are tabled; a map of another extent is not used.
    def hazard_map(raster_path: str) -> dict:
        return {"flood": {"label": "GFM (Sentinel-1)", "raster": raster_path},
                "maps": {"essential_services": {"png": "", "layers": ["drinking_water"]}},
                "exposure": {"drinking_water": {
                    "label": "drinking water facilities", "located": 2,
                    "in_hazard": {"flood_extent": {"in": 1, "out": 1, "not_observed": 0,
                                                   "names_in": ["Intake West"]}}}}}

    run.cache_input("map_hazard_exposure", hazard_map(str(raster)))
    text = render(run, "essential_services_hev").read_text(encoding="utf-8")
    check("facilities are tested against the extent: the western one is flooded",
          "| drinking water facilities | 2 | 1 | 1 | 0 | Intake West |" in text,
          text[text.find("| drinking"):][:120])
    run.cache_input("map_hazard_exposure", hazard_map(str(root / "another.tif")))
    text = render(run, "essential_services_hev").read_text(encoding="utf-8")
    check("a map drawn for another extent is not used",
          "drawn for another flood extent" in text)


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            test_templates(root)
            test_registry_and_gaps(root)
            test_window_and_how_sure(root)
            test_land_cover(root)
            test_hev(root)
    finally:
        logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
