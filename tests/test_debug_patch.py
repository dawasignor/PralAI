"""Monty's retry contract: what happens to a patch that cannot work.

    python tests/test_debug_patch.py

Offline -- Argos is faked, so no provider is touched.

Motivated by a live run: Argos correctly diagnosed "a runtime environment
issue, not a parameter issue", answered `fix: "drop"`, then re-sent all four
tasks unchanged. Attempt 3 was byte-identical to attempt 2 and the last retry
was spent for nothing, with nothing in the transcript to show it.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# langgraph is a real dependency of the repo but not of this check, and the
# functions under test never touch it.
if "langgraph" not in sys.modules:                          # pragma: no cover
    stub = types.ModuleType("langgraph")
    graph_stub = types.ModuleType("langgraph.graph")
    graph_stub.END, graph_stub.START, graph_stub.StateGraph = "END", "START", object
    sys.modules["langgraph"], sys.modules["langgraph.graph"] = stub, graph_stub

from core import graph                                      # noqa: E402
from core.state import new_state                            # noqa: E402

FAILURES: list[str] = []

TASKS = [
    {"function": "fetch_weather_data", "params": {"latitude": 25.5}},
    {"function": "fetch_terrain_data", "params": {"bbox": [91.8, 25.5, 91.9, 25.6]}},
]


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def fake_argos(reply: dict):
    """Patch the graph's agent factory to answer with one canned decision."""
    agent = types.SimpleNamespace(
        run=lambda task, **kw: json.dumps(reply),
        spec=types.SimpleNamespace(debug_prompt="debug"),
    )
    graph._agent = lambda slug: agent


def failed_state(attempts: int = 1) -> dict:
    state = dict(new_state("analyse the flood"))
    state["monty_attempts"] = attempts
    state["monty_error"] = {
        "event_id": 42,
        "tasks": TASKS,
        "task": "fetch_terrain_data",
        "error_type": "runtime_error",
        "message": "ModuleNotFoundError: No module named 'rasterio'",
        "failed": ["result_2"],
        "succeeded": ["result_1"],
        "stage": "execute_tasks",
    }
    state["monty_result"] = {"results": {
        "result_1": {"task": "fetch_weather_data", "status": "success", "output": {}},
    }}
    return state


def logged(update: dict) -> str:
    return " | ".join(update.get("exchange_log", []))


def check_abandon() -> None:
    """fix=abandon must stop now, not spend the remaining retries."""
    fake_argos({"diagnosis": "missing dependency, not a parameter problem",
                "fix": "abandon", "patched_tasks": TASKS})
    update = graph._argos_debug_turn(failed_state())

    print(f"  route   : {update['route']}")
    if update["route"] != "finish":
        fail(f"abandon did not stop the loop: route={update['route']}")
    if "abandon" not in logged(update):
        fail("the abandon was not recorded in the exchange log")
    else:
        print("  logged  : abandon")

    # Two halves since 2026-10-15: a plain answer for the reader, and the
    # technical report -- attempts, survivors, diagnosis -- for a developer.
    report = update.get("failure_report", "")
    if "FAILED after" not in report:
        fail("abandon did not produce the give-up report")
    if "result_1" not in report:
        fail("the partial success was thrown away on abandon")
    else:
        print("  keeps the successful task: yes")
    if "missing dependency" not in report:
        fail("Argos' diagnosis is missing from the report")
    else:
        print("  carries the diagnosis: yes")
    answer = update.get("answer", "")
    if "There was an error while running the" not in answer:
        fail(f"the answer does not say plainly which workflow failed: {answer!r}")
    for internal in ("FAILED", "result_", "Traceback", "fetch_", "Monty"):
        if internal in answer:
            fail(f"the answer shows an internal term {internal!r}: {answer!r}")
    print("  answer  : plain, no internal terms")


def check_noop_patch_is_reported() -> None:
    """Re-sending the same list is allowed, but must not look like a real fix."""
    fake_argos({"diagnosis": "retrying", "fix": "params", "patched_tasks": TASKS})
    update = graph._argos_debug_turn(failed_state())

    if update["route"] != "monty":
        fail("a no-op patch should still be attempted, not blocked")
    if "patch-noop" not in logged(update):
        fail("an identical patch was not reported")
    else:
        print("  logged  : patch-noop, attempt proceeds")


def check_drop_that_drops_nothing() -> None:
    """fix=drop while keeping every task contradicts itself."""
    fake_argos({"diagnosis": "dropping the broken one", "fix": "drop",
                "patched_tasks": [TASKS[0], dict(TASKS[1], params={"bbox": [1, 2, 3, 4]})]})
    update = graph._argos_debug_turn(failed_state())

    log_text = logged(update)
    if "patch-contradiction" not in log_text:
        fail(f"a drop that removed nothing was not reported: {log_text}")
    else:
        print("  logged  : patch-contradiction")
    if "patch-noop" in log_text:
        fail("a changed list was wrongly called a no-op")


def check_real_drop_is_quiet() -> None:
    """A genuine drop gets no complaint."""
    fake_argos({"diagnosis": "terrain cannot run here", "fix": "drop",
                "patched_tasks": [TASKS[0]]})
    update = graph._argos_debug_turn(failed_state())

    log_text = logged(update)
    for noise in ("patch-noop", "patch-contradiction"):
        if noise in log_text:
            fail(f"a genuine drop was flagged as {noise}")
    print(f"  logged  : clean ({len(update['monty_debug']['patched_tasks'])} task left)")
    if update["route"] != "monty":
        fail("a genuine drop should be retried")


def check_exhausted_still_gives_up() -> None:
    """The existing bound still holds -- abandon does not replace it."""
    fake_argos({"diagnosis": "x", "fix": "params", "patched_tasks": TASKS})
    update = graph._argos_debug_turn(failed_state(attempts=3))
    if update["route"] != "finish":
        fail("retries were exhausted but the loop continued")
    else:
        print("  route   : finish (budget exhausted)")


def check_markdown_answer_parses() -> None:
    """An answer containing a markdown table must survive the parser.

    Argos writes markdown, and a model emitting a table puts real newlines
    inside the JSON string. Strict JSON rejects that, and the fallback then
    hands the whole envelope back as the answer -- which is what a user reads.
    """
    nl = chr(10)
    answer = nl.join(["## Result", "", "| Rank | Action |", "|---|---|",
                      "| 1 | Safe water |"])
    # Built with real newlines on purpose: that is exactly what the model
    # emitted inside the JSON string, and what strict json.loads rejects.
    reply = '{"next": "finish", "brief": "done", "answer": "' + answer + '"}'

    parsed = graph._parse_decision(reply)
    print(f"  next    : {parsed.get('next')}")
    if parsed.get("next") != "finish":
        fail(f"a markdown answer did not parse: next={parsed.get('next')}")
    got = parsed.get("answer", "")
    if got.lstrip().startswith("{"):
        fail("the whole JSON envelope came back as the answer")
    elif got != answer:
        fail(f"the answer was altered: {got[:60]!r}")
    else:
        print("  answer  : extracted intact, table and all")

    # Genuinely unparseable text must still fall back rather than raise.
    junk = graph._parse_decision("I could not decide.")
    if junk.get("answer") != "I could not decide.":
        fail("the fallback for unparseable text was lost")
    else:
        print("  fallback: still works for non-JSON")


def check_one_task_per_delegation() -> None:
    """Argos asking for three workflows runs one, and says which it held back.

    Asked about rainfall, Argos sent terrain, permanent water and land cover
    alongside the weather -- three results at once, with the answer to the
    actual question buried in them.
    """
    seen: dict = {}

    def fake_run_monty(request, attempt=1, debug=None):
        seen["tasks"] = [t["function"] for t in request["tasks"]]
        return {
            "results": {"result_1": {"task": request["tasks"][0]["function"],
                                     "status": "success", "output": {}}},
            "tasks": request["tasks"],
            "exchange_log": [],
            "execution_logs": ["ran one"],
        }

    original = graph.run_monty
    graph.run_monty = fake_run_monty
    try:
        state = dict(new_state("rainfall over Shillong"))
        state["monty_request"] = {"event_id": 7, "tasks": [
            {"function": "fetch_weather_data", "params": {}},
            {"function": "fetch_terrain_data", "params": {}},
            {"function": "fetch_permanent_water", "params": {}},
        ]}
        update = graph.monty_node(state)
    finally:
        graph.run_monty = original

    print(f"  sent to monty : {seen['tasks']}")
    if seen["tasks"] != ["fetch_weather_data"]:
        fail(f"more than one task reached Monty: {seen['tasks']}")
    else:
        print("  ok  only the first task ran")

    finding = update["findings"][0]
    blob = json.dumps(finding, default=str)
    for name in ("fetch_terrain_data", "fetch_permanent_water"):
        if name not in blob:
            fail(f"{name} was held back without being named in the finding")
    if "fetch_terrain_data" in blob and "fetch_permanent_water" in blob:
        print("  ok  the deferred workflows are named, not silently dropped")

    # The request recorded on the state is what actually ran, so a retry
    # rebuilds from one task rather than resurrecting the other two.
    ran = [t["function"] for t in update["monty_request"]["tasks"]]
    if ran != ["fetch_weather_data"]:
        fail(f"the recorded request still carries the deferred tasks: {ran}")
    else:
        print("  ok  a retry would rebuild from the one task that ran")


def main() -> int:
    hr("1. fix=abandon stops immediately")
    check_abandon()
    hr("2. an identical patch is reported, not blocked")
    check_noop_patch_is_reported()
    hr("3. a drop that drops nothing is reported")
    check_drop_that_drops_nothing()
    hr("4. a genuine drop is not flagged")
    check_real_drop_is_quiet()
    hr("5. the retry budget still ends the loop")
    check_exhausted_still_gives_up()
    hr("6. a markdown answer survives the parser")
    check_markdown_answer_parses()
    hr("7. one workflow per delegation")
    check_one_task_per_delegation()

    print()
    if FAILURES:
        print(f"{'=' * 68}\n{len(FAILURES)} CHECK(S) FAILED\n{'=' * 68}")
        for message in FAILURES:
            print(f"  - {message}")
        return 1
    print(f"{'=' * 68}\nALL CHECKS PASSED\n{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
