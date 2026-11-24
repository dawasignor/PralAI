"""The cache toggle: off re-fetches, and never writes the experiment record.

    python tests/test_cache_policy.py       # no model, no network, no cost

Under `off` every tool re-fetches (`use_cache=False`
where a tool takes it), and the run reads and writes only `runs/<run_id>/`.
The experiment's inputs are the frozen thesis record, so an uncached run must
not be able to write them -- by any path, including Monty's keepers.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (cache_policy, experiment, graph, logbook, session,   # noqa: E402
                  session_store, workflows)
from core.experiment import Experiment                               # noqa: E402
from core.monty_graph import run_monty                               # noqa: E402
from core.session import SessionRecord                               # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


SEEN: list[object] = []


def _cached_tool(bbox: str = "", use_cache: bool = True) -> dict:
    SEEN.append(use_cache)
    return {"use_cache_seen": use_cache}


def _plain_tool() -> dict:
    return {"value": 1}


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_parse_and_scope() -> None:
    ok = all(cache_policy.parse(v) == "on" for v in ("on", "ON", True, "true", 1))
    ok &= all(cache_policy.parse(v) == "off" for v in ("off", False, "false", 0, "no"))
    check("on/off read from flags, booleans and strings", ok)
    try:
        cache_policy.parse("sometimes")
        check("an unreadable policy is refused", False)
    except ValueError:
        check("an unreadable policy is refused", True)
    check("the default is on, outside any run",
          cache_policy.current() == "on" and cache_policy.run_id() == "")
    with cache_policy.use("off") as rid:
        inside = (cache_policy.is_off(), cache_policy.run_id())
    check("use() sets the policy and mints a run id", inside == (True, rid) and rid,
          rid)
    check("and restores both afterwards",
          cache_policy.current() == "on" and cache_policy.run_id() == "")


def test_record_is_not_written(tmp: Path) -> None:
    exp = Experiment("frozen-event", root=tmp / "experiments")
    exp.create()
    exp.cache_input("weather", {"total": 390.97})
    before = _files(tmp / "experiments")
    refused = []
    with cache_policy.use("off", "rq5-test"):
        for label, call in (
                ("input", lambda: exp.cache_input("weather", {"total": 1.0})),
                ("output", lambda: exp.write("weather_data", "body")),
                ("deliverable", lambda: exp.write_deliverable("brief", "text"))):
            try:
                call()
            except cache_policy.CachePolicyError:
                refused.append(label)
    check("off refuses every write to an experiment (input, output, deliverable)",
          refused == ["input", "output", "deliverable"], str(refused))
    check("the experiment folder is byte-for-byte unchanged",
          _files(tmp / "experiments") == before
          and exp.cached_input("weather")["payload"] == {"total": 390.97})
    with cache_policy.use("off", "rq5-test"):
        store = cache_policy.run_store()
        store.create()
        store.cache_input("weather", {"total": 1.0})
    check("the run store writes under runs/<run_id>/inputs/",
          (tmp / "runs" / "rq5-test" / "inputs" / "weather.json").exists())
    with cache_policy.use("on"):
        exp.cache_input("weather", {"total": 390.97})
    check("on still writes the experiment as before",
          exp.cached_input("weather")["payload"] == {"total": 390.97})


def test_session_store_follows_policy(tmp: Path) -> None:
    rec = SessionRecord(session_id="s-cache")
    on = session_store.for_record(rec)
    with cache_policy.use("off", "rq5-test"):
        off = session_store.for_record(rec)
        off_none = session_store.for_record(None)
    check("on: the session folder", on is not None and "s-cache" in str(on.dir))
    check("off: the run folder, with or without a record",
          isinstance(off, cache_policy.RunStore) and off.dir == tmp / "runs" / "rq5-test"
          and isinstance(off_none, cache_policy.RunStore))


def test_use_cache_flag() -> None:
    SEEN.clear()
    with cache_policy.use("off", "rq5-test"):
        run_monty({"event_id": 1, "tasks": [
            {"function": "t_cached", "params": {"use_cache": True}},
            {"function": "t_plain"}]})
    with cache_policy.use("on", "on-test"):
        run_monty({"event_id": 1, "tasks": [{"function": "t_cached"}]})
    check("off passes use_cache=False even when the caller sent True",
          SEEN[:1] == [False], str(SEEN))
    check("on leaves a tool's own default alone", SEEN[1:] == [True], str(SEEN))


def test_monty_node_keepers(tmp: Path) -> None:
    exp_root = tmp / "experiments"
    exp = Experiment("kelani-test", root=exp_root)
    exp.create()
    exp.cache_input("plain", {"value": 0})
    before = _files(exp_root)
    rec = SessionRecord(session_id="s-keepers")
    state = {"monty_request": {"event_id": 1, "tasks": [{"function": "t_plain"}]},
             "experiment": "kelani-test", "session": rec}
    lines: list[str] = []
    with cache_policy.use("off", "rq5-keepers"), logbook.collect(lines):
        graph.monty_node(state)
    check("an uncached Monty batch leaves the named experiment untouched",
          _files(exp_root) == before, str(_files(exp_root)))
    check("and keeps its result in runs/<run_id>/inputs/",
          (tmp / "runs" / "rq5-keepers" / "inputs" / "t_plain.json").exists())
    check("the bypass is logged as cache-state",
          any("cache-state" in ln and "kelani-test" in ln for ln in lines))
    check("nothing is written to the session folder",
          not (session.SESSIONS_DIR / "s-keepers" / "inputs").exists())
    with cache_policy.use("on", "on-keepers"):
        graph.monty_node(state)
    check("cached: the experiment receives the result as before",
          exp.cached_input("t_plain") is not None)


def main() -> None:
    workflows.tool("t_cached", "test fake with use_cache")(_cached_tool)
    workflows.tool("t_plain", "test fake")(_plain_tool)
    saved = (cache_policy.RUNS_DIR, experiment.ROOT, session.SESSIONS_DIR,
             logbook._log_path)
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        cache_policy.RUNS_DIR = tmp / "runs"
        experiment.ROOT = tmp / "experiments"
        session.SESSIONS_DIR = tmp / "sessions"
        logbook._log_path = lambda: None
        try:
            test_parse_and_scope()
            test_record_is_not_written(tmp)
            test_session_store_follows_policy(tmp)
            test_use_cache_flag()
            test_monty_node_keepers(tmp)
        finally:
            (cache_policy.RUNS_DIR, experiment.ROOT, session.SESSIONS_DIR,
             logbook._log_path) = saved
            workflows.REGISTRY.pop("t_cached", None)
            workflows.REGISTRY.pop("t_plain", None)
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
