"""The 2026-11-02 fixes: Norwood's comments after a fetch, the sensitivity
chart, the thesis path's new steps and the answer format rule.

No model and no network: the agent is a fake and the chart is drawn from a
payload computed here. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FETCHES = ("fetch_weather", "fetch_lulc_data", "fetch_worldcover", "fetch_terrain_data",
           "fetch_permanent_water")


def _monty(task: str) -> dict:
    return {"results": {"result_1": {"task": task, "status": "success", "output": {}}}}


def test_every_fetch_is_claimed_by_the_commentary() -> None:
    from core.graph import _pending_interpretation

    for task in FETCHES:
        got = _pending_interpretation({"monty_result": _monty(task), "flows_run": []})
        assert got == ("norwood", "event_commentary"), (task, got)
    done = {"monty_result": _monty("fetch_weather"), "flows_run": ["norwood:event_commentary"]}
    assert _pending_interpretation(done) is None
    assert _pending_interpretation({"monty_result": _monty("where_am_i"), "flows_run": []}) is None


def test_accuracy_still_goes_to_the_uncertainty_agent() -> None:
    from core.graph import _pending_interpretation

    got = _pending_interpretation({"monty_result": _monty("accuracy_metrics"), "flows_run": []})
    assert got == ("entro", "accuracy_interpretation"), got


class _Spec:
    name = "Agent Norwood"


class _Fake:
    spec = _Spec()

    def __init__(self, reply: str):
        self.reply = reply

    def run(self, *a, **k) -> str:
        return self.reply


def _node(reply: str) -> dict:
    from core import graph

    saved = graph._agent
    graph._agent = lambda slug: _Fake(reply)
    try:
        return graph.make_specialist_node("norwood")({
            "flow": "event_commentary", "task": "t", "brief": "b", "findings": [],
            "monty_result": _monty("fetch_weather"), "session": None, "experiment": ""})
    finally:
        graph._agent = saved


def test_comments_go_under_the_answer_and_none_are_forced() -> None:
    out = _node("- **Rain fell on wet ground**: 120 mm in 36 h.")
    assert out["appendix"].startswith("- **Rain fell"), out
    for reply in ("No comment.", "no comments", "  No comment"):
        assert "appendix" not in _node(reply), reply


def test_commentary_flow_reads_the_summaries_only() -> None:
    from core.projection import project
    from core.spec import load_flow

    flow = load_flow("norwood", "event_commentary")
    assert flow.append_to_answer and set(flow.after_workflows) == set(FETCHES)
    weather = {"meta": {}, "hourly": [1] * 100, "event_summary": {"total_mm": 1}}
    assert "hourly" not in project(weather, "weather_grid", flow.consumes_schema)
    assert "weather_grid" in load_flow("norwood", "pre_flood_aoi_brief").consumes


def test_sensitivity_draws_its_chart() -> None:
    from core.workflows.sensitivity import sensitivity_analysis

    with tempfile.TemporaryDirectory() as tmp:
        out = sensitivity_analysis(91.7, 3.2, 40, 5, 30, output_dir=tmp, event_id="t")
        assert out["figure"]["name"] == "sensitivity_stability"
        assert Path(out["figure"]["path"]).is_file()
        assert "figure" not in sensitivity_analysis(91.7, 3.2, 40, 5, 30, write_figure=False)


def test_module_8_shows_the_chart() -> None:
    from core import report_render
    from core.experiment import Experiment
    from core.workflows.sensitivity import sensitivity_analysis

    assert "{{figure:sensitivity_figure}}" in report_render.template("sensitivity")
    with tempfile.TemporaryDirectory() as tmp:
        store = Experiment("sens-2026", root=Path(tmp))
        store.create(place="p", window="2026-09-24 to 2026-09-30")
        store.cache_input("sensitivity_analysis",
                          sensitivity_analysis(91.7, 3.2, 40, 5, 30, write_figure=False))
        body = report_render.render(store, "sensitivity").read_text(encoding="utf-8")
        assert "sensitivity_stability.png" in body, body[:400]


def test_thesis_path_names_jrc_water_and_modules_9_and_10() -> None:
    from core import thesis_path

    steps = {s["id"]: s for s in thesis_path.view(None)}
    assert steps["water"]["label"] == "JRC water bodies"
    assert steps["essential"]["output"] == "Module 9"
    assert steps["health"]["output"] == "Module 10"


def test_argus_is_told_to_format_for_scanning() -> None:
    from core import spec

    prompt = spec.load_spec("argos").system_prompt
    assert "Numbers and lists go in points, not sentences" in prompt
    assert "Bold the key phrases" in prompt


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__} {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
