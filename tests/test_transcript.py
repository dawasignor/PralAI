"""The pinned session and its transcript, without checkpoints.

    python tests/test_transcript.py      # no model, no network, no cost

Checkpoints (likes, frozen modules, rollback, lock-in) were removed on
2026-11-23 (user decision). What stays:

    1. pinned     -- the session's AOI and window are the hardcoded ones on
                     every load, and a message that would change them is refused
    2. transcript -- a console turn of the pinned session is appended to
                     transcript.jsonl and replayed when the console opens it;
                     other sessions keep none
    3. no freeze  -- the store overwrites a stored result, Monty runs the tool,
                     nothing is moved aside on open, and the like route is gone
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import pinned, resolve, session, transcript  # noqa: E402
from core.session import Place, SessionRecord  # noqa: E402

results: list[tuple[str, str]] = []
SID = pinned.SESSION_ID


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def scripted(**fields):
    payload = {"place": "", "approx_start": "", "approx_end": "", "change": "none",
               "chosen_place_index": None, "chosen_event_index": None,
               "about_event": False, **fields}
    return lambda situation, message: json.dumps(payload)


def no_lookup(*a, **k):
    raise AssertionError("the pinned session must not look a place up")


def main() -> int:
    from core.session_store import SessionStore

    with tempfile.TemporaryDirectory() as tmp:
        session.SESSIONS_DIR = Path(tmp)

        # 1. pinned ---------------------------------------------------------
        record = session.load(SID)
        check("pinned: a new record carries the hardcoded AOI and window, confirmed",
              record.place.bbox == pinned.PLACE["bbox"] and record.window.start == "2025-11-26"
              and record.window.end == "2025-12-04" and record.confirmed)
        record.place = Place(display="Somewhere else", bbox=(1.0, 2.0, 3.0, 4.0))
        record.pending = {"switch": {"place": None, "window": None, "message": "x"}}
        session.save(record)
        again = session.load(SID)
        check("pinned: a stored record with another AOI is reset on load, switch dropped",
              again.place.bbox == pinned.PLACE["bbox"] and not again.pending)
        other = SessionRecord(session_id="s-other")
        other.place = Place(display="Boston", bbox=(-71.2, 42.2, -70.8, 42.4))
        session.save(other)
        check("pinned: any other session is left alone",
              session.load("s-other").place.display == "Boston")
        outcome = resolve.resolve(again, "now look at the flooding in Colombo",
                                  scripted(place="Colombo", change="new"), no_lookup, no_lookup)
        check("pinned: a new place is refused, not asked about, and nothing moves",
              outcome["asked"] and "cannot be changed" in outcome["answer"]
              and again.place.bbox == pinned.PLACE["bbox"] and not again.pending,
              outcome["answer"][:80])
        outcome = resolve.resolve(again, "use 1 Dec to 10 Dec 2025 instead",
                                  scripted(approx_start="2025-12-01", approx_end="2025-12-10",
                                           change="same"), no_lookup, no_lookup)
        check("pinned: new dates are refused too",
              outcome["asked"] and again.window.end == "2025-12-04", outcome["answer"][:60])

        # 2. transcript through the server ----------------------------------
        from api import console_server as cs

        calls = []

        def fake_run(task, **kw):
            calls.append(task)
            st = SessionStore(kw["session_id"])
            st.create()
            st.cache_input("weather_grid", {"value": len(calls)})
            return {"answer": f"answer {len(calls)}", "session": session.load(kw["session_id"]),
                    "route": "monty", "flows_run": [], "exchange_log": []}

        real = cs.run_task
        cs.run_task = fake_run
        try:
            out1 = cs.execute_node({"sessionId": SID, "input": {"task": "weather please"}})
            out2 = cs.execute_node({"sessionId": SID, "input": {"task": "weather again"}})
            cs.execute_node({"sessionId": "s-other", "input": {"task": "hello"}})
        finally:
            cs.run_task = real
        kept = transcript.turns(SID)
        check("transcript: each pinned console turn is appended, in order",
              [t["task"] for t in kept] == ["weather please", "weather again"]
              and [t["id"] for t in kept] == ["turn-001", "turn-002"], str(kept)[:200])
        check("transcript: a run's answer carries no like or turn controls",
              "turn" not in out1 and "turn" not in out2
              and all("liked" not in s for s in out2.get("thesisPath") or []))
        check("transcript: another session keeps none",
              not (session.folder("s-other") / "transcript.jsonl").exists())

        # 3. nothing is frozen ----------------------------------------------
        store = SessionStore(SID)
        check("no freeze: the second fetch overwrote the first",
              (store.cached_input("weather_grid") or {}).get("payload") == {"value": 2})
        opened = cs.open_session({})
        check("open: the pinned id, its AOI and the replayed turns, nothing rolled back",
              opened["sessionId"] == SID and len(opened["turns"]) == 2
              and opened["session"]["bbox"] == list(pinned.PLACE["bbox"])
              and "checkpoints" not in opened and "restored" not in opened
              and not (session.folder(SID) / "rolled_back").exists()
              and (store.cached_input("weather_grid") or {}).get("payload") == {"value": 2})
        handler_routes = cs.Handler.do_POST.__code__.co_consts
        check("server: the like route is gone",
              not any(isinstance(c, str) and "checkpoint" in c for c in handler_routes))

    import core
    check("code: no checkpoint module is left",
          not (Path(core.__file__).parent / "checkpoints.py").exists())
    from core import monty_graph
    import inspect
    check("code: Monty runs every tool, no kept result is returned",
          "reuse" not in inspect.getsource(monty_graph._run_one))

    failed = [n for s, n in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
