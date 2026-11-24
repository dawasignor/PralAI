"""The thesis path: the outputs in order, and which step comes next.

    pytest tests/test_thesis_path.py
    python tests/test_thesis_path.py        # no model, no network, no cost

The console draws this as a flowchart and Argus recommends its next step, so
both read one definition. Held still here: a step is done only for this
session's AOI and window; the next step is the first undone step whose needs
are done; this turn's fetch counts before the record is saved; an unregistered
workflow is left out and nothing waits on it; the offer needs an AOI and a
window; the path appears in Argus' prompt; and the routing turn falls back to
the path's next step when Argus makes no offer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import thesis_path as tp                              # noqa: E402
from core.session import Place, SessionRecord, Window           # noqa: E402


def record(*fetched: str, flows: tuple[str, ...] = (), where: bool = True) -> SessionRecord:
    """A session with `fetched` run. `where` -- Where am I, the path's first
    step -- is done unless asked otherwise, so the tests below walk the path
    from Availability as they did before it was added (2026-11-01)."""
    rec = SessionRecord(session_id="tp")
    rec.place = Place(name="Kelani", display="Kelani River, Sri Lanka",
                      bbox=(79.8, 6.783, 80.217, 7.083))
    rec.window = Window(start="2025-11-24", end="2025-12-01")
    rec.workflows_run = (["where_am_i"] if where else []) + list(fetched)
    rec.flows_run = list(flows)
    return rec


def status(steps: list[dict]) -> dict[str, str]:
    return {s["id"]: s["status"] for s in steps}


# Output 1, the availability check, comes first of the analysis (D-33); the tests that walk further along the path start with it done.
A = "data_availability"


def test_fresh_session_starts_at_where_am_i() -> None:
    """Where am I is the path's first step (user decision 2026-11-01)."""
    got = status(tp.view(record(where=False)))
    assert got["where"] == "next", got
    assert list(got.values()).count("next") == 1, got
    steps = {s["id"]: s for s in tp.view(record(where=False))}
    assert all("where" not in s["needs"] for s in steps.values()),         "nothing waits on Where am I"


def test_after_where_am_i_comes_availability() -> None:
    got = status(tp.view(record()))
    assert got["availability"] == "next", got
    assert list(got.values()).count("next") == 1, got
    assert got["where"] == "done", got
    assert all(v == "todo" for k, v in got.items() if k not in ("availability", "where")), got


def test_next_is_first_undone_with_needs_met() -> None:
    got = status(tp.view(record(A, "fetch_weather")))
    assert got["weather"] == "done" and got["lulc"] == "next", got
    # The brief needs all three rasters; with two of them it is not offered.
    got = status(tp.view(record(A, "fetch_weather", "fetch_lulc_data",
                                "fetch_terrain_data")))
    assert got["water"] == "next" and got["brief"] == "todo", got
    got = status(tp.view(record(A, "fetch_weather", "fetch_lulc_data",
                                "fetch_terrain_data", "fetch_permanent_water")))
    assert got["brief"] == "next", got


def test_old_tool_name_still_counts() -> None:
    # Records written before 2026-10-25 carry fetch_weather_grid.
    got = status(tp.view(record(A, "fetch_weather_grid")))
    assert got["weather"] == "done" and got["lulc"] == "next", got


def test_worldcover_stands_in_for_land_cover() -> None:
    got = status(tp.view(record(A, "fetch_weather", "fetch_worldcover")))
    assert got["lulc"] == "done" and got["terrain"] == "next", got


def test_flow_step_is_done_by_flows_run() -> None:
    rasters = (A, "fetch_weather", "fetch_lulc_data", "fetch_terrain_data",
               "fetch_permanent_water")
    got = status(tp.view(record(*rasters, flows=("norwood:pre_flood_aoi_brief",))))
    assert got["brief"] == "done", got
    # A flow of the same name on another agent is not this step.
    got = status(tp.view(record(*rasters, flows=("citadel:pre_flood_aoi_brief",))))
    assert got["brief"] == "next", got


def test_this_turn_counts_before_the_record_is_saved() -> None:
    got = status(tp.view(record(A), extra_workflows=["fetch_weather"]))
    assert got["weather"] == "done" and got["lulc"] == "next", got


def test_ranking_waits_for_its_inputs() -> None:
    got = status(tp.view(record("fetch_weather", "fetch_lulc_data",
                                "fetch_terrain_data", "fetch_permanent_water",
                                flows=("norwood:pre_flood_aoi_brief",))))
    # Availability has no needs and comes first (D-33); ranking needs it.
    assert got["availability"] == "next" and got["ranking"] == "todo", got


def test_unregistered_workflow_is_left_out(monkeypatch=None) -> None:
    original = tp._registered
    tp._registered = lambda: {s["name"] for s in tp.STEPS
                              if s["kind"] == "workflow" and s["id"] != "ranking"}
    try:
        steps = tp.view(record())
    finally:
        tp._registered = original
    ids = [s["id"] for s in steps]
    assert "ranking" not in ids, ids
    # Nothing waits on a node that is not there.
    assert all("ranking" not in s["needs"] for s in steps), steps


def test_offer_needs_an_aoi_and_a_window() -> None:
    rec = record()
    offer = tp.suggestion(rec)
    assert offer and offer["workflow"] == "data_availability", offer
    assert offer["why"].startswith("Next on the thesis path"), offer
    rec.window = Window()
    assert tp.suggestion(rec) is None
    assert tp.suggestion(None) is None


def test_flow_offer_names_the_flow() -> None:
    offer = tp.suggestion(record(A, "fetch_weather", "fetch_lulc_data",
                                 "fetch_terrain_data", "fetch_permanent_water"))
    assert offer["flow"] == "pre_flood_aoi_brief" and offer["workflow"] == "", offer


def test_prompt_block_names_the_next_step() -> None:
    block = tp.prompt_block(record(A, "fetch_weather"))
    assert block.startswith("## Thesis path"), block
    assert "[done] Availability (Module 1)" in block, block
    assert "[done] Weather (Module 2)" in block, block
    assert "[NEXT] Land cover" in block and "next_step" in block, block


def test_route_turn_falls_back_to_the_path(monkeypatch=None) -> None:
    """Argus finishes with no next_step: the graph offers the path's next step."""
    from core import graph

    class FakeArgus:
        class spec:                                            # noqa: N801
            resolver_prompt = debug_prompt = ""
        def run(self, task, **_):                              # noqa: ANN001
            FakeArgus.seen = task
            return '{"next": "finish", "answer": "done"}'

    original = graph._agent
    graph._agent = lambda slug: FakeArgus()
    try:
        state = {"task": "what was the rainfall?", "steps": 0, "findings": [],
                 "session": record(A),
                 "monty_result": {"results": {"result_1": {
                     "task": "fetch_weather", "status": "success"}}}}
        update = graph._argos_route_turn(state)
    finally:
        graph._agent = original
    assert "## Thesis path" in FakeArgus.seen
    # The weather was fetched THIS turn, so the offer is the next step after it.
    assert update["next_step"]["workflow"] == "fetch_lulc_data", update.get("next_step")
    assert any("suggest-path" in line for line in update["exchange_log"])


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
