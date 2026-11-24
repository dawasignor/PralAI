"""Modules 11 and 12: facts from stored results, the sidecar, the guard, the lint.

    python tests/test_report_render.py      # no model, no network, no cost

Held still here, on a synthetic store and template:

    1. guard       -- a report before Modules 1-10 of the same store is refused (D-24)
    2. facts       -- every placeholder becomes a fact with its source and status;
                      the sidecar lists every one; llm slots stay pending or are cut
                      to their word limit
    3. no flood    -- the best-ranked product mapping no flood makes every flood
                      fact NOT KNOWN, with that reason (D-24)
    4. chips       -- the status rule: AFFECTED / NOT AFFECTED / NOT KNOWN
    5. template    -- the live template is the fenced block of the vault note
    6. lint        -- filler, US spelling, mixed dates, a fixed number in a
                      template, an untraceable number in a report; the renderer's
                      own markers and a rounded fact are not flagged
    7. plain       -- bridges counted by name, not by OSM way; runs still to do
                      apart from new data, in plain words; "A and B" in the heading
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import lint_report                                      # noqa: E402
from core import logbook, report_render, vault          # noqa: E402
from core.experiment import Experiment                  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []

TEMPLATE = """**{{fact:event_name}}** -- {{fact:aoi_display_name}}
## 1. What caused it
{{tile:storm_total_mm|icon=rain}} {{tile:peak_1h_mm|icon=rain-peak}}
{{llm:cause_one_liner|max_words=5}}
## 2. People and land
{{tile:flooded_km2|icon=land}} {{meter:flooded_pct|min=0|max=100}}
{{meter:inform_risk|min=0|max=10}}
### Roads {{chip:status_roads}}
{{fact:new_data_needed}}
{{fact:no_such_fact}}"""


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def _store(root: Path) -> Experiment:
    run = Experiment("report-test", root=root)
    run.create(place="Test AOI", window="2025-11-24 to 2025-12-01")
    return run


def _write_modules(run: Experiment) -> None:
    for slot in report_render.REQUIRED:
        run.write(slot, "rendered for the test")


def _raster(path: Path, bbox: list[float]) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    arr = np.zeros((50, 50), dtype="uint8")
    arr[:, :25] = 1
    with rasterio.open(path, "w", driver="GTiff", height=50, width=50, count=1, dtype="uint8",
                       crs="EPSG:4326", nodata=255, transform=from_bounds(*bbox, 50, 50)) as dst:
        dst.write(arr, 1)


def test_guard_and_facts(root: Path) -> None:
    run = _store(root)
    try:
        report_render.render(run, "public_report", template_text=TEMPLATE)
        check("a report before Modules 1-10 is refused (D-24)", False)
    except ValueError as exc:
        check("a report before Modules 1-10 is refused (D-24)", "D-24" in str(exc))
    _write_modules(run)
    run.cache_input("weather_grid", {"event_summary": {"storm_total_mm": {"aoi_mean": 390.97},
                                                      "max_intensity_mm": {"1h": 15.14}}})
    run.cache_input("rank_flood_products", {"ranking": [
        {"id": "gfm", "alternative": "GFM (Sentinel-1)", "rank": 1}]})
    run.cache_input("get_flood_extent", {"product": "gfm", "label": "GFM (Sentinel-1)",
                                         "status": "no_flood_mapped", "flood_km2": 0.0})
    run.cache_input("inform_risk", {"scores": {"inform_risk": {"value": 3.3}},
                                    "release": {"name": "INFORM Risk Mid 2025", "published": "2025-08-28"}})
    path = report_render.render(run, "public_report", template_text=TEMPLATE,
                                       llm={"cause_one_liner": "one two three four five six seven"})
    text = path.read_text(encoding="utf-8")
    side = json.loads(path.with_suffix(".facts.json").read_text(encoding="utf-8"))
    facts = {f["key"]: f for f in side["facts"]}
    check("a stored value is a fact with its unit and source, shown at 3 significant figures",
          facts["storm_total_mm"]["value"] == 390.97 and "fetch_weather" in facts["storm_total_mm"]["source"]
          and "**391** mm storm total" in text)
    check("the sidecar lists every non-llm placeholder",
          {"event_name", "storm_total_mm", "flooded_km2", "flooded_pct", "inform_risk",
           "status_roads", "new_data_needed", "no_such_fact"} <= set(facts))
    check("no flood mapped makes the flood facts NOT KNOWN, with the D-24 reason",
          facts["flooded_km2"]["status"] == "not_known" and "maps no flood" in facts["flooded_km2"]["reason"]
          and "*flooded: not known yet*" in text)
    check("a short tile's reason is listed at the end, not crammed into the line",
          "### Not known yet" in text and "maps no flood" in text.split("### Not known yet")[1])
    # Never cut since 2026-11-22: kept whole and flagged.
    check("an llm slot over its word limit is kept whole, flagged, and marked written",
          "one two three four five six seven" in text and "_(Over its 5-word limit: 7 words.)_" in text
          and " ..." not in text and side["llm_slots"][0]["status"] == "written")
    check("a placeholder the renderer does not know is recorded, not dropped",
          side["unknown_placeholders"] == ["no_such_fact"] and facts["no_such_fact"]["status"] == "not_known")
    check("the chip is NOT KNOWN without a mapped extent", "**[NOT KNOWN]**" in text)
    check("the heading is Module 11 with its source",
          "# Module 11: Public report" in text)

    bbox = [79.94, 6.905, 80.09, 6.978]
    raster = root / "extent.tif"
    _raster(raster, bbox)
    run.cache_input("get_flood_extent", {"product": "gfm", "label": "GFM (Sentinel-1)",
                                         "status": "flood_mapped", "flood_km2": 60.4321,
                                         "flood_fraction_of_observed": 0.5, "coverage": 0.95,
                                         "observed_km2": 120.0, "raster": {"path": str(raster)}})
    run.cache_input("assess_road_access", {"roads_km_total": {"flooded_km": 3.2}, "hospitals": []})
    text = report_render.render(run, "public_report", template_text=TEMPLATE).read_text(encoding="utf-8")
    check("with the extent mapped, the flood facts are filled from it",
          "**60.4** km2 flooded" in text and "**50%** of the observed land flooded" in text)
    check("a flooded road makes roads AFFECTED", "**[AFFECTED]**" in text)
    check("an unwritten llm slot is a visible pending marker",
          "_(to be written: cause_one_liner, at most 5 words)_" in text)
    check("the heading joins its last source with 'and'",
          text.split("\n# Module 11", 1)[1].split("\n", 1)[0].count(" and ") >= 1)


def test_plain_wording(root: Path) -> None:
    from core.outputs import _bridges_line
    from core.workflows.impact import group_bridges

    ways = [{"name": "Kaduwela Bridge", "flooded_m": 40}, {"name": "Kaduwela Bridge", "flooded_m": 38},
            {"name": "", "osm": "way/1"}, {"name": None, "osm": "way/2"}]
    g = group_bridges(ways)
    check("bridge ways are grouped into bridges by name",
          g == {"segments": 4, "named": ["Kaduwela Bridge"], "named_count": 1, "unnamed_sections": 2})
    line = _bridges_line(ways)
    check("the bridge line counts bridges, not ways, and shows no OSM id",
          "1 named (Kaduwela Bridge)" in line and "2 unnamed" in line and "4 mapped segments" in line
          and "way/" not in line, line)

    run = Experiment("plain-test", root=root)
    run.create(place="Test AOI", window="2025-11-24 to 2025-12-01")
    _write_modules(run)
    text = report_render.render(run, "public_report", template_text=TEMPLATE).read_text(encoding="utf-8")
    tail = text.split("**[NOT KNOWN]**")[-1]
    check("a run still to do is listed apart from new data, in plain words",
          "Still to run in this analysis:" in tail and "event weather" in tail
          and "fetch_weather" not in tail, tail[:300])


def test_template_block() -> None:
    saved = (vault.build_index, vault.read_note)
    vault.build_index = lambda *a, **k: None
    vault.read_note = lambda index, title: {"content": "## What this is\ntext\n\n## Template\n\n"
                                            "```markdown\n**{{fact:event_name}}**\n## 1. A\n```\n"}
    try:
        got = report_render.template("public_report")
    finally:
        vault.build_index, vault.read_note = saved
    check("the live template is the fenced block of the vault note",
          got == "**{{fact:event_name}}**\n## 1. A")


def test_lint(root: Path) -> None:
    tpl = root / "tpl.md"
    tpl.write_text("---\ntype: template\n---\n## Template\n\n```markdown\n## 1. Cause\n"
                   "{{tile:x}} It is important to note the color of the water.\n"
                   "> Even 10 cm of water is a risk.\nThe river rose 3 m.\n```\n", encoding="utf-8")
    r = lint_report.lint(tpl)
    checks = {i["check"] for i in r["issues"]}
    check("a template's filler, US spelling and fixed number are flagged",
          r["mode"] == "template" and {"filler", "spelling", "numbers"} <= checks)
    check("the allowed advisory number is not flagged",
          not any("'10'" in i["detail"] for i in r["issues"]))

    rep = root / "11_public_report.md"
    rep.write_text("---\nn: 11\n---\n# Module 11\n**60.43** km2 flooded on 2025-11-28.\n"
                   "Not known yet -- a.\nNot known yet -- b.\nNot known yet -- c.\n"
                   "The water reached 7.5 m on 28 November 2025.\n", encoding="utf-8")
    rep.with_suffix(".facts.json").write_text(json.dumps({"facts": [{"value": 60.4321}]}),
                                              encoding="utf-8")
    r = lint_report.lint(rep)
    nums = [i["detail"] for i in r["issues"] if i["check"] == "numbers"]
    check("a rounded fact traces; a number in no fact does not",
          any("'7.5'" in d for d in nums) and not any("60.43" in d for d in nums), str(nums))
    check("mixed date formats are flagged", any(i["check"] == "dates" for i in r["issues"]))
    check("the renderer's Not-known markers are not counted as repeated openers",
          not any(i["check"] == "openers" for i in r["issues"]))


def test_facility_states(root: Path) -> None:
    """Not observed is never 'not affected', and nothing reached is one line
    (user decisions 2026-10-26, BUG-025 and BUG-026)."""
    import rasterio
    from rasterio.transform import from_bounds

    run = Experiment("facility-test", root=root)
    run.create(place="Test AOI", window="2025-11-24 to 2025-12-01")
    _write_modules(run)
    bbox = [79.94, 6.905, 80.09, 6.978]
    raster = root / "extent_states.tif"
    arr = np.zeros((50, 50), dtype="uint8")
    arr[:, :25] = 255                                   # west half: not observed; east: dry
    with rasterio.open(raster, "w", driver="GTiff", height=50, width=50, count=1, dtype="uint8",
                       crs="EPSG:4326", nodata=255, transform=from_bounds(*bbox, 50, 50)) as dst:
        dst.write(arr, 1)
    run.cache_input("rank_flood_products", {"ranking": [
        {"id": "gfm", "alternative": "GFM (Sentinel-1)", "rank": 1}]})
    run.cache_input("get_flood_map", {"product": "gfm", "label": "GFM (Sentinel-1)",
                                      "status": "flood_mapped", "flood_km2": 1.0, "coverage": 0.5,
                                      "observed_km2": 50.0, "raster": {"path": str(raster)}})
    west = [{"name": f"Well {i}", "lon": 79.96, "lat": 6.92 + 0.01 * i, "tags": {}} for i in range(3)]
    template = "{{chip:status_water}}\n{{fact:water_facilities_checklist}}\n"

    def render(items):
        run.cache_input("where_am_i", {"facilities": {"categories": {
            "water_facilities": {"items": items, "count": len(items)}}}})
        return report_render.render(run, "executive_summary",
                                           template_text=template).read_text(encoding="utf-8")

    text = render(west)
    check("every facility unobserved -> NOT OBSERVED, never NOT AFFECTED",
          "**[NOT OBSERVED]**" in text and "NOT AFFECTED" not in text)
    check("nothing reached is one line with the dry / not-observed counts",
          "No major facilities affected (3 located: 0 on observed dry ground, 3 on ground" in text
          and "- [ ]" not in text)
    text = render(west + [{"name": "East Tower", "lon": 80.07, "lat": 6.95, "tags": {}}])
    check("one observed dry facility and none flooded -> NOT AFFECTED",
          "**[NOT AFFECTED]**" in text and "1 on observed dry ground" in text)


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            test_guard_and_facts(root)
            test_plain_wording(root)
            test_facility_states(root)
            test_template_block()
            test_lint(root)
    finally:
        logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
