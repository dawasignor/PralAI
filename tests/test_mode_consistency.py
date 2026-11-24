"""The three mode pipelines agree on every number.

    python tests/test_mode_consistency.py      # no model, no network, no cost

On a cached fixture (synthetic payloads, written by this test):

    1. consistency -- flood_analyst, flood_bot and chanakya run in order;
                      every fact the three facts sidecars share is identical
                      (value, unit, source, status, reason, text), apart from
                      the facts about the report itself (REPORT_SCOPED); the
                      check is shown to catch a changed fact
    2. order       -- Modules 1-10 are written by flood_analyst; a report
                      pipeline before them is refused and fetches nothing (D-24)
    3. agents      -- with a writer, a report's slots are written from its
                      facts; with none, they stay pending and are recorded;
                      a cut-off agent reply is not stored
    4. failure     -- a failing tool is recorded and the pipeline goes on;
                      a stored result for another window is neither reused
                      nor overwritten
    5. bounds      -- a step past its time ceiling stops the pipeline
    6. records     -- each run writes its record and metrics; an uncached run
                      writes only under runs/<run_id>/
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import cache_policy, logbook, report_config, report_render  # noqa: E402
from core import pipelines                                            # noqa: E402
from core.experiment import Experiment                                # noqa: E402
from core.pipelines import base                                       # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
SLUG = "mode-test"
BBOX = [79.94, 6.905, 80.09, 6.978]
WINDOW = "2025-11-24 to 2025-12-01"

TEMPLATES = {
    "public_report": """**{{fact:event_name}}** -- {{fact:aoi_display_name}} · {{fact:window}}
Updated {{fact:last_updated}}
{{llm:headline|max_words=20}}
{{tile:flooded_km2|icon=land}} {{meter:flooded_pct|min=0|max=100}} {{tile:people_in_aoi}}
{{meter:inform_risk|min=0|max=10}} {{tile:gdp_usd_bn}} {{fact:flood_product}} {{fact:confidence}}
### Roads {{chip:status_roads}}
{{figure:flood_overlay}}
{{fact:search_cutoff}}
{{llm:what_to_do|max_words=40}}
{{figure:uncertainty_traffic_lights}}
{{fact:new_data_needed}}
{{fact:sources_major}}""",
    "executive_summary": """**{{fact:event_name}}** -- {{fact:aoi_display_name}} · {{fact:window}}
{{llm:bottom_line|max_words=70}}
{{tile:flooded_km2}} {{tile:people_in_aoi}} {{tile:roads_flooded_km}} {{tile:hospitals_access_at_risk}}
### Roads {{chip:status_roads}}
{{fact:hospital_access_table}}
{{fact:bridges_affected}}
{{fact:flood_product}} {{fact:confidence}} {{meter:inform_risk|min=0|max=10}}
{{llm:decision_now|max_words=40}}
{{figure:uncertainty_traffic_lights}}
{{fact:new_data_needed}}""",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


# -- the fixture -------------------------------------------------------------------
def _raster(path: Path) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    arr = np.zeros((40, 40), dtype="uint8")
    arr[:, :16] = 1                      # flooded
    arr[:4, :] = 2                       # permanent water
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=40, width=40, count=1, dtype="uint8",
                       crs="EPSG:4326", nodata=255, transform=from_bounds(*BBOX, 40, 40)) as dst:
        dst.write(arr, 1)


def fixture(root: Path) -> Experiment:
    """A store holding what the reports read -- the ranking and sensitivity
    computed by the real (offline) tools, the rest synthetic."""
    from core.workflows import mcdm, sensitivity

    run = Experiment(SLUG, root=root)
    run.create(place="Test reach", window=WINDOW)
    idx = dict(cloud_pct=91.67, wind_ms=4.13, veg_pct=61.26, steep_pct=9.85, urban_pct=0.19)
    rank = mcdm.rank_flood_products(**idx)
    top = min(rank["ranking"], key=lambda r: r["rank"])
    raster = root / "extent.tif"
    _raster(raster)
    stored = {
        "rank_flood_products": rank,
        "sensitivity_analysis": sensitivity.sensitivity_analysis(**idx),
        "get_flood_extent": {"product": top["id"], "label": top["alternative"],
                             "status": "flood_mapped", "flood_km2": 42.4321,
                             "flood_fraction_of_observed": 0.4, "coverage": 0.93,
                             "observed_km2": 106.1, "raster": {"path": str(raster)}},
        "assess_road_access": {"roads_km_total": {"flooded_km": 12.5}, "hospitals_access_at_risk": 1,
                               "hospitals": [{"name": "A Hospital", "site": "dry",
                                              "nearest_flooded_road_m": 120.0, "access_at_risk": True},
                                             {"name": "", "site": "dry",
                                              "nearest_flooded_road_m": 900.0, "access_at_risk": False}],
                               "bridges_in_flood_extent": [{"name": "Old Bridge", "flooded_m": 30},
                                                           {"name": "Old Bridge", "flooded_m": 28},
                                                           {"name": "", "osm": "way/7"}]},
        "population": {"estimate": {"population": 123456, "range": [110000, 140000]},
                       "vintage": "census 2024"},
        "world_bank_indicators": {"indicators": {"gdp_usd": {"value": 84.4e9, "year": 2024},
                                                 "population": {"value": 21900000, "year": 2024}}},
        "inform_risk": {"country": "Sri Lanka", "scores": {"inform_risk": {"value": 3.3}},
                        "release": {"name": "INFORM Risk Mid 2025", "published": "2025-08-28"}},
        "search_previous_disasters": {"search_cutoff": "2025-11-23", "events": []},
        # Fetched for ANOTHER window: must be neither reused nor overwritten.
        "data_availability": {"products": []},
    }
    for name, payload in stored.items():
        params = {"bbox": BBOX, "start_date": "2025-11-21", "end_date": "2025-12-02"} \
            if name == "data_availability" else {}
        run.cache_input(name, payload, params=params)
    return run


class FakeWriter:
    """Answers an agent call without a model: slot texts from the task's own
    slot list, or a fixed text for a flow."""

    def __init__(self, reply: str = ""):
        self.calls: list[base.AgentCall] = []
        self.reply = reply

    def __call__(self, call: base.AgentCall) -> str:
        self.calls.append(call)
        if self.reply:
            return self.reply
        if not call.flow:
            slots = [ln[2:].split(":")[0] for ln in call.task.splitlines()
                     if ln.startswith("- ") and ": at most " in ln]
            return json.dumps({s: f"Written for {s}." for s in slots})
        return f"Text for {call.flow}."


def refuse(entry: dict) -> dict:
    raise AssertionError(f"a cached-only run called {entry['function']}")


def _facts(path: Path) -> dict[str, dict]:
    side = json.loads(path.read_text(encoding="utf-8"))
    keep = ("value", "unit", "source", "status", "reason", "text")
    return {f["key"]: {k: f.get(k) for k in keep} for f in side["facts"]}


def disagreements(sidecars: dict[str, dict[str, dict]]) -> list[str]:
    """Every shared, event-scoped fact that differs between two sidecars."""
    out = []
    names = list(sidecars)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for key in sorted(set(sidecars[a]) & set(sidecars[b]) - set(report_render.REPORT_SCOPED)):
                if sidecars[a][key] != sidecars[b][key]:
                    out.append(f"{key}: {a} != {b}")
    return out


# -- the tests ---------------------------------------------------------------------
def test_consistency(root: Path) -> None:
    store = fixture(root)
    try:
        pipelines.get("flood_bot").run(SLUG, root=root, writer=None, cached_only=True, executor=refuse)
        check("a report pipeline before the Flood analyst run is refused (D-24)", False)
    except pipelines.PipelineRefused as exc:
        check("a report pipeline before the Flood analyst run is refused (D-24)", "D-24" in str(exc))

    writer = FakeWriter()
    records = {name: pipelines.get(name).run(SLUG, root=root, writer=writer, cached_only=True,
                                             run_id="mode-test-run", executor=refuse)
               for name in pipelines.ORDER}
    check("all three pipelines finish (no step stopped)",
          all(r["status"] in ("ok", "degraded") for r in records.values()),
          str({n: r["status"] for n, r in records.items()}))
    written = {s["slot"] for s in store.status() if s["written"]}
    check("the Flood analyst run writes Modules 1-10, the reports 11 and 12",
          set(report_render.REQUIRED) | {"public_report", "executive_summary"} <= written,
          str(sorted(set(report_render.REQUIRED) - written)))
    failed_renders = [s["name"] for r in records.values() for s in r["steps"]
                      if s["kind"] in ("render", "report", "facts") and s["status"] == "failed"]
    check("no render step failed", not failed_renders, str(failed_renders))

    sidecars = {"flood_analyst": _facts(Path(store.dir) / "pipeline_runs" / "flood_analyst.facts.json"),
                "flood_bot": _facts(store.path("public_report").with_suffix(".facts.json")),
                "chanakya": _facts(store.path("executive_summary").with_suffix(".facts.json"))}
    diffs = disagreements(sidecars)
    shared = set(sidecars["flood_bot"]) & set(sidecars["chanakya"]) - set(report_render.REPORT_SCOPED)
    known = [k for k in shared if sidecars["flood_bot"][k]["status"] != "not_known"]
    check("every shared fact is identical across the three sidecars (D-3)", not diffs, "; ".join(diffs[:5]))
    check("the comparison is not vacuous: several shared facts are known",
          len(known) >= 6, f"{len(known)} known of {len(shared)} shared: {sorted(known)}")
    changed = json.loads(json.dumps(sidecars))
    changed["chanakya"]["flooded_km2"]["value"] = 42.44
    check("a changed fact is caught and named", disagreements(changed) ==
          ["flooded_km2: flood_analyst != chanakya", "flooded_km2: flood_bot != chanakya"],
          str(disagreements(changed)))

    pub = store.read("public_report")
    check("the report's slots are written from its facts by the writer",
          "Written for headline." in pub and "Written for what_to_do." in pub
          and sum(1 for c in writer.calls if not c.flow) == 2)
    slot_task = next(c.task for c in writer.calls if not c.flow)
    check("the slot writer is given the facts, and NOT KNOWN facts as such",
          "flooded_km2: 42.4321 km2 flooded" in slot_task and "NOT KNOWN" in slot_task)
    check("the event header travels with every agent call",
          all(str(BBOX) in c.context and "2025-11-24 to 2025-12-01" in c.context for c in writer.calls))
    check("Chanakya's writer is handed Modules 9 and 10",
          any("essential services hev (the Flood analyst run)" in c.context for c in writer.calls))

    avail = next(s for s in records["flood_analyst"]["steps"] if s["name"] == "data_availability"
                 and s["kind"] == "tool")
    check("a stored result for another window is not reused",
          avail["status"] == "skipped" and "start_date 2025-11-21" in avail["detail"], avail["detail"])
    check("... and not overwritten",
          (store.cached_input("data_availability") or {}).get("params", {}).get("start_date") == "2025-11-21")
    items = records["flood_analyst"]["uncertainty"]["pipeline"]
    check("the pipeline uncertainty records the stale input and the cache state",
          any(i["key"] == "data_availability_stale" for i in items) and any(i["key"] == "cache" for i in items))
    side = json.loads(store.path("public_report").with_suffix(".facts.json").read_text(encoding="utf-8"))
    lights = {r[0]: r for r in next(f for f in side["facts"] if f["key"] == "uncertainty_traffic_lights")["value"]}
    check("the report's uncertainty lights count the pipeline level", lights["pipeline"][1] > 0, str(lights["pipeline"]))
    rec = Path(store.dir) / "pipeline_runs" / "chanakya.json"
    run_rec = cache_policy.RUNS_DIR / "mode-test-run" / "pipelines" / "chanakya.json"
    metrics = cache_policy.RUNS_DIR / "mode-test-run" / "metrics.jsonl"
    check("each run writes its record to the store and the run folder, with metrics",
          rec.exists() and run_rec.exists() and metrics.exists()
          and '"kind": "pipeline"' in metrics.read_text(encoding="utf-8"))


def test_agents_off_and_cut_off(root: Path) -> None:
    fixture(root)
    pipelines.get("flood_analyst").run(SLUG, root=root, writer=None, cached_only=True, executor=refuse)
    rec = pipelines.get("flood_bot").run(SLUG, root=root, writer=None, cached_only=True, executor=refuse)
    step = rec["steps"][-1]
    check("with agents off, the slots stay pending and are recorded",
          step["status"] == "pending" and any(i["key"] == "public_report_slots_pending"
                                              for i in rec["uncertainty"]["pipeline"]))
    store = Experiment(SLUG, root=root)
    p = base.Pipeline("t", "t", [base.agent("citadel", "drinking_water_scarcity", lambda r: "task")])
    out = p.run(SLUG, root=root, writer=FakeWriter("half a reply [TRUNCATED: max_tokens]"), executor=refuse)
    check("a cut-off agent reply is not stored",
          out["steps"][0]["status"] == "failed" and store.deliverable("drinking_water_scarcity") is None)
    out = p.run(SLUG, root=root, writer=FakeWriter("A whole reply."), executor=refuse)
    check("a whole reply is stored as the flow's deliverable",
          out["steps"][0]["status"] == "ok"
          and (store.deliverable("drinking_water_scarcity") or {}).get("text") == "A whole reply.")


def test_failure_and_bounds(root: Path) -> None:
    fixture(root)
    store = Experiment(SLUG, root=root)

    def fail(entry: dict) -> dict:
        return {"task": entry["function"], "output": None, "status": "error",
                "message": "boom", "error": {"error_type": "runtime_error", "message": "boom",
                                             "exception": "RuntimeError"}}

    p = base.Pipeline("t", "t", [base.tool("fetch_terrain_data", lambda r: {"bbox": r.bbox}),
                                 base.render("terrain")])
    out = p.run(SLUG, root=root, writer=None, executor=fail)
    check("a failing tool is recorded and the pipeline goes on",
          [s["status"] for s in out["steps"]] == ["failed", "ok"] and out["status"] == "degraded"
          and store.failure("terrain") is not None)

    def slow(run):
        time.sleep(3)
        return {"status": "ok"}

    p = base.Pipeline("t", "t", [base.Step("tool", "slow", slow, max_s=0.5), base.render("terrain")])
    out = p.run(SLUG, root=root, writer=None, executor=refuse)
    check("a step past its time ceiling stops the pipeline",
          [s["status"] for s in out["steps"]] == ["timed_out", "not_run"] and out["status"] == "stopped")

    before = sorted(p.name for p in store.inputs_dir.glob("*.json"))
    rec = pipelines.get("flood_analyst").run(SLUG, root=root, cache="off", run_id="mode-test-off",
                                             writer=None, cached_only=True, executor=refuse)
    after = sorted(p.name for p in store.inputs_dir.glob("*.json"))
    run_store = cache_policy.RUNS_DIR / "mode-test-off"
    check("an uncached run writes only under runs/<run_id>/, never the experiment",
          before == after and rec["store"] == str(run_store)
          and (run_store / "01_data_availability.md").exists(), rec["store"])
    check("an uncached run reads only what it fetched itself",
          all(s["status"] != "cached" for s in rec["steps"]))


def main() -> None:
    saved = (logbook._log_path, cache_policy.RUNS_DIR, report_render.template,
             dict(report_config.EXPERIMENTS))
    logbook._log_path = lambda: None
    # Test templates for the two reports; Modules 1-10 render from their live
    # vault templates, as in a real run.
    live = report_render.template
    report_render.template = lambda slot: TEMPLATES[slot] if slot in TEMPLATES else live(slot)
    report_config.EXPERIMENTS[SLUG] = {"event_name": "Test flood", "climate_zone": "tropical",
                                       "country_iso3": "LKA", "bbox": BBOX}
    try:
        for test in (test_consistency, test_agents_off_and_cut_off, test_failure_and_bounds):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                cache_policy.RUNS_DIR = root / "runs"
                test(root / "experiments")
    finally:
        logbook._log_path, cache_policy.RUNS_DIR, report_render.template = saved[:3]
        report_config.EXPERIMENTS.clear()
        report_config.EXPERIMENTS.update(saved[3])
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
