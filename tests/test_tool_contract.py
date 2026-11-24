"""The tool contract: blocks added, contract-missing logged, failures unchanged.

    python tests/test_tool_contract.py      # no model, no network, no cost

Every tool result gains an `uncertainty` block (four
levels), a `sources` block and a `run` block, added by Monty's runner when the
tool did not return them. That must never fail a task, never overwrite what a
tool did return, and never change the failure shape -- the five keys Argos'
debugger and the console read.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cache_policy, logbook, observability, workflows   # noqa: E402
from core.monty_graph import run_monty                             # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
FIVE_KEYS = {"task", "output", "status", "message", "error"}


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


# Fake tools, registered for this test only and removed afterwards.
def _bare(x: int = 1) -> dict:
    return {"value": x}


def _complete() -> dict:
    return {"value": 2,
            "uncertainty": {"input": [{"key": "k", "statement": "s", "value": 1,
                                       "unit": "", "basis": "measured",
                                       "vintage": "", "vault_note": ""}],
                            "tool": [], "pipeline": [], "decision": []},
            "sources": [{"id": "src", "title": "A source"}]}


def _legacy_sources() -> dict:
    return {"value": 3, "sources": ["OpenStreetMap, ODbL"]}


def _own_run_key() -> dict:
    return {"value": 4, "run": "something the tool meant"}


def _boom() -> dict:
    raise RuntimeError("upstream exploded")


def _flaky_source() -> dict:
    observability.source_call(
        "https://overpass-api.de/api/interpreter?data=[out:json];node(1)", ok=False,
        status=502, reason="at capacity")
    return {"value": 5}


def _not_a_dict() -> list:
    return [1, 2, 3]


FAKES = {"t_bare": _bare, "t_complete": _complete, "t_legacy": _legacy_sources,
         "t_runkey": _own_run_key, "t_boom": _boom, "t_flaky": _flaky_source,
         "t_list": _not_a_dict}


def _run(*tasks: dict, lines: list[str] | None = None) -> dict:
    sink: list[str] = lines if lines is not None else []
    with logbook.collect(sink):
        return run_monty({"event_id": "t", "tasks": list(tasks)}).get("results") or {}


def test_apply_contract_unit() -> None:
    out, notes = workflows.apply_contract({"a": 1}, {"run_id": "r"})
    check("a bare payload gains all three blocks",
          set(workflows.CONTRACT_KEYS) <= set(out) and out["a"] == 1)
    check("the uncertainty block has the four levels, each a list",
          all(isinstance(out["uncertainty"][lvl], list)
              for lvl in workflows.UNCERTAINTY_LEVELS))
    check("the additions are named", notes == ["uncertainty", "sources"], str(notes))
    _, notes = workflows.apply_contract(_complete(), {"run_id": "r"})
    check("a complete payload adds nothing worth noting", notes == [], str(notes))
    out, notes = workflows.apply_contract(_legacy_sources(), {})
    check("a legacy sources list keeps its shape and is noted",
          out["sources"] == ["OpenStreetMap, ODbL"] and "sources legacy shape" in notes)
    out, _ = workflows.apply_contract({"uncertainty": {"tool": "one item"}}, {})
    check("a lone item at a level is kept, wrapped in a list",
          out["uncertainty"]["tool"] == ["one item"] and out["uncertainty"]["input"] == [])
    out, notes = workflows.apply_contract([1], {})
    check("a non-object output passes through untouched, with a note",
          out == [1] and notes and "list" in notes[0])
    check("@workflow is still an alias of @tool", workflows.workflow is workflows.tool)


def test_through_monty() -> None:
    lines: list[str] = []
    with cache_policy.use("on", "contract-test"):
        res = _run({"function": "t_bare", "params": {"x": 7}}, lines=lines)
    r = res["result_1"]
    out = r["output"]
    check("the task still succeeds and keeps its own keys",
          r["status"] == "success" and out["value"] == 7)
    check("the result entry still has exactly the five keys", set(r) == FIVE_KEYS)
    run = out.get("run", {})
    check("the run block carries id, policy, timing and attempts",
          run.get("run_id") == "contract-test" and run.get("cache") == "on"
          and isinstance(run.get("duration_s"), float) and run.get("attempts") == 1
          and run.get("started_at", "").endswith("Z") and run.get("source_failures") == [],
          str(run))
    check("contract-missing is logged for a bare tool",
          any("contract-missing" in ln and "t_bare" in ln for ln in lines))
    check("tool-start and tool-end are logged around it",
          any("tool-start" in ln and "t_bare" in ln for ln in lines)
          and any("tool-end" in ln and "t_bare" in ln and "status=success" in ln
                  for ln in lines))

    lines = []
    res = _run({"function": "t_complete"}, lines=lines)
    out = res["result_1"]["output"]
    check("a tool that fills its blocks is not reported as missing them",
          not any("contract-missing" in ln for ln in lines))
    check("its own uncertainty and sources are kept as returned",
          out["uncertainty"]["input"][0]["basis"] == "measured"
          and out["sources"][0]["id"] == "src")

    res = _run({"function": "t_runkey"})
    out = res["result_1"]["output"]
    check("a tool's own run key is kept under run.tool_run, not lost",
          out["run"].get("tool_run") == "something the tool meant"
          and "run_id" in out["run"])

    res = _run({"function": "t_list"})
    check("a non-object output still succeeds unchanged",
          res["result_1"]["status"] == "success" and res["result_1"]["output"] == [1, 2, 3])


def test_failures_unchanged() -> None:
    res = _run({"function": "t_boom"}, {"function": "no_such_tool"},
               {"function": "t_bare", "params": {"y": 1}})
    shapes = {k: set(v) for k, v in res.items()}
    check("runtime error, unknown name and bad signature all have the five keys",
          all(s == FIVE_KEYS for s in shapes.values()), str(shapes))
    types = [res[k]["error"]["error_type"] for k in ("result_1", "result_2", "result_3")]
    check("their error types are unchanged",
          types == ["runtime_error", "unknown_function", "signature_mismatch"], str(types))
    check("a failure gets no contract blocks (output stays null)",
          all(res[k]["output"] is None for k in res))
    check("message still mirrors error.message",
          all(res[k]["message"] == res[k]["error"]["message"] for k in res))


def test_source_failures() -> None:
    with cache_policy.use("on", "contract-test"):
        res = _run({"function": "t_flaky"})
    fails = res["result_1"]["output"]["run"]["source_failures"]
    check("a failed upstream call lands in run.source_failures",
          len(fails) == 1 and fails[0]["status"] == 502, str(fails))
    check("it names the domain only -- no path, no query string",
          fails[0]["domain"] == "overpass-api.de"
          and "interpreter" not in str(fails) and "out:json" not in str(fails))
    before = observability.sample("pralai_tool_calls_total", tool="t_boom", status="error")
    _run({"function": "t_boom"})
    after = observability.sample("pralai_tool_calls_total", tool="t_boom", status="error")
    check("the RED counter counts a failed call", after == before + 1, f"{before} -> {after}")


def main() -> None:
    for name, fn in FAKES.items():
        workflows.tool(name, f"test fake {name}")(fn)
    saved_log = logbook._log_path
    logbook._log_path = lambda: None               # keep logs/exchange.log clean
    try:
        test_apply_contract_unit()
        test_through_monty()
        test_failures_unchanged()
        test_source_failures()
    finally:
        logbook._log_path = saved_log
        for name in FAKES:
            workflows.REGISTRY.pop(name, None)
        # The run folder this test's metrics went to.
        import shutil
        shutil.rmtree(cache_policy.RUNS_DIR / "contract-test", ignore_errors=True)
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
