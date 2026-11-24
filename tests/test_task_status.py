"""Starting a run in the background, and asking how it is going.

    python tests/test_task_status.py        # no model, no network, no cost

`run_task` is replaced with a stand-in that logs a few lines and sleeps. What
is under test is the machinery around it -- that a start returns immediately,
that steps appear while the run is still going, that elapsed time is reported,
and that a crash becomes an answer rather than a poll that never resolves.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import console_server as api                          # noqa: E402
from core import logbook                                       # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def fake_run(steps: int = 3, pause: float = 0.12, boom: bool = False):
    """A stand-in for the graph: logs as it goes, then returns a state."""
    # **kw: the real signature grows (experiment= was the last one), and a
    # fake that pins every argument turns an addition into ten unrelated
    # failures that all read like a broken server.
    def run(task, persona="", verified_sources_only=False,
            session_id="", **kw):
        logbook.log("run", None, "start", task)
        for i in range(steps):
            time.sleep(pause)
            logbook.log("argos", "citadel", "delegate", f"step {i + 1}")
        if boom:
            raise RuntimeError("the graph fell over")
        return {"answer": "done", "exchange_log": [], "session": None,
                "route": "argos", "steps": steps, "flows_run": [],
                "monty_result": {}}
    return run


def with_fake(run, fn):
    original = api.run_task
    api.run_task = run
    try:
        return fn()
    finally:
        api.run_task = original


REQUEST = {"node": "argos", "sessionId": "s-test",
           "input": {"task": "how were the land conditions pre-event?"},
           "persona": "chanakya", "verifiedSourcesOnly": False}


def test_start_returns_immediately() -> None:
    def body():
        began = time.time()
        started = api.start_task(REQUEST)
        took = time.time() - began
        check("starting a run returns before the run finishes",
              took < 0.15, f"{took * 1000:.0f} ms to return")
        check("it hands back a task id to poll",
              started.get("taskId", "").startswith("t-"), started.get("taskId"))
        check("and says it is running", started["status"] == "running")
        return started["taskId"]

    task_id = with_fake(fake_run(steps=3, pause=0.12), body)
    _wait(task_id)


def test_progress_is_visible_while_running() -> None:
    """The point of the whole thing: steps appear before the answer does."""
    def body():
        task_id = api.start_task(REQUEST)["taskId"]
        time.sleep(0.30)                 # two of three steps in
        mid = api.task_status(task_id)
        check("a run in flight reports itself as running",
              mid["status"] == "running", mid["status"])
        check("steps taken so far are already visible",
              len(mid["events"]) >= 2, f"{len(mid['events'])} event(s)")
        check("elapsed time is reported and increasing",
              mid["elapsedMs"] >= 250, f"{mid['elapsedMs']} ms")
        check("the answer is withheld until there is one",
              "reply" not in mid)
        return task_id

    task_id = with_fake(fake_run(steps=3, pause=0.15), body)
    done = _wait(task_id)
    check("the finished run carries its answer", done.get("reply") == "done")
    check("and its final status", done["status"] == "ok", done["status"])
    check("and the time the whole run took",
          done["elapsedMs"] >= 450, f"{done['elapsedMs']} ms")


def test_elapsed_stops_at_the_finish() -> None:
    """Elapsed is the run's duration, not the age of the record."""
    def body():
        task_id = api.start_task(REQUEST)["taskId"]
        return _wait(task_id)["elapsedMs"], task_id

    at_finish, task_id = with_fake(fake_run(steps=1, pause=0.05), body)
    time.sleep(0.35)
    later = api.task_status(task_id)["elapsedMs"]
    check("a finished run does not keep counting",
          later == at_finish, f"{at_finish} ms then {later} ms")


def test_a_crash_becomes_an_answer() -> None:
    def body():
        return _wait(api.start_task(REQUEST)["taskId"])

    done = with_fake(fake_run(steps=1, pause=0.02, boom=True), body)
    check("a run that raises comes back as an error, not a hung poll",
          done["status"] == "error", done["status"])
    # Plain language for the reader (the guardrail); the exception itself goes
    # to the session log, where a developer looks.
    check("and says so plainly, with no exception in the reply",
          "error" in done.get("reply", "") and "RuntimeError" not in done.get("reply", ""),
          done.get("reply"))
    check("and the session log says what went wrong",
          any("the graph fell over" in (e.get("detail") or "")
              for e in done.get("events") or []), str(done.get("events")))


def test_unknown_task_id() -> None:
    answer = api.task_status("t-neverexisted")
    check("an unknown task id tells the caller to stop polling",
          answer["status"] == "unknown", answer["status"])
    check("and explains why rather than returning nothing",
          "no longer being followed" in answer.get("reply", ""))


def test_runs_do_not_see_each_others_lines() -> None:
    """Two runs at once, each collecting only its own steps."""
    seen: dict[str, int] = {}

    def one(tag: str, steps: int):
        # **kw for the same reason as above: a fake that pins every argument
        # turns a signature addition into unrelated failures.
        def run(task, persona="", verified_sources_only=False,
                session_id="", **kw):
            for i in range(steps):
                time.sleep(0.05)
                logbook.log("argos", None, "delegate", f"{tag} step {i}")
            return {"answer": tag, "exchange_log": [], "session": None,
                    "monty_result": {}}
        return run

    # Both jobs run through the same module-level run_task slot, so the fake
    # has to dispatch on the task text rather than being swapped per job.
    runs = {"alpha": one("alpha", 2), "bravo": one("bravo", 5)}

    def dispatch(task, **kw):
        return runs[task](task, **kw)

    def body():
        ids = {tag: api.start_task({**REQUEST, "input": {"task": tag}})["taskId"]
               for tag in runs}
        for tag, task_id in ids.items():
            seen[tag] = len(_wait(task_id)["events"])
        return ids

    with_fake(dispatch, body)
    check("a two-step run collected only its own two lines",
          seen.get("alpha") == 2, f"alpha saw {seen.get('alpha')}")
    check("a five-step run collected only its own five",
          seen.get("bravo") == 5, f"bravo saw {seen.get('bravo')}")


def _wait(task_id: str, limit: float = 10.0) -> dict:
    deadline = time.time() + limit
    while time.time() < deadline:
        answer = api.task_status(task_id)
        if answer["status"] != "running":
            return answer
        time.sleep(0.02)
    raise AssertionError(f"{task_id} never finished")


def test_finished_survives_a_real_state() -> None:
    """`_finished` must run against a real state, not only a fake one.

    Every check above stubs `run_task`, so nothing exercised the response
    assembly itself -- and a NameError in it (`WORKFLOWS` left behind after the
    set was replaced by a registry lookup) reached a live run and failed it
    after 95 seconds of real work. The graph had already succeeded; the crash
    was purely in describing what it did.
    """
    state = {
        "answer": "Done.",
        "route": "argos",
        "steps": 1,
        "exchange_log": [
            "23:08:49  monty  .  run  result_1 fetch_weather(...)",
            "23:08:54  monty  -> argos  OK  attempt=1 1/1 tasks ok",
        ],
        "flows_run": [],
        "next_step": {},
        "monty_result": {"status": "success", "results": {}},
    }
    try:
        payload = api._finished({"input": {"task": "x"}}, "s-1", state,
                                   list(state["exchange_log"]))
    except NameError as exc:
        fail(f"_finished raised NameError on a real state: {exc}")
        return
    except Exception as exc:                                # noqa: BLE001
        fail(f"_finished raised {type(exc).__name__}: {exc}")
        return

    ran = payload.get("state", {}).get("workflowsRun")
    print(f"  workflowsRun: {ran}")
    if ran != ["fetch_weather"]:
        fail(f"a workflow named in the log was not reported: {ran}")
    else:
        print("  ok  a registered workflow in the log is reported")
    if payload.get("reply") != "Done.":
        fail("the answer did not survive assembly")


def test_workflow_names_come_from_the_registry() -> None:
    """The server must not keep its own copy of the workflow list.

    It did, as a hardcoded set of four, and two registered workflows were
    invisible to it -- a log line naming one was never tagged with it.
    """
    from core import workflows

    names = api._workflow_names()
    print(f"  server sees {len(names)}, registry has {len(workflows.names(include_inactive=True))}")
    if names != set(workflows.names(include_inactive=True)):
        fail(f"server list differs from the registry: "
             f"{sorted(names ^ set(workflows.names(include_inactive=True)))}")
    else:
        print("  ok  the two agree")


def main() -> int:
    # The sink is a ContextVar, and a thread starts from a copy of the context
    # that spawned it -- so a stray sink left set here would be inherited.
    check("no log sink is set before the first run",
          logbook._SINK.get() is None)

    test_finished_survives_a_real_state()
    test_workflow_names_come_from_the_registry()
    test_start_returns_immediately()
    test_progress_is_visible_while_running()
    test_elapsed_stops_at_the_finish()
    test_a_crash_becomes_an_answer()
    test_unknown_task_id()
    test_runs_do_not_see_each_others_lines()

    check("no task thread is still running at the end",
          not [t for t in threading.enumerate() if t.name.startswith("task-")],
          str([t.name for t in threading.enumerate() if t.name.startswith("task-")]))

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
