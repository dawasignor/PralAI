"""Paced calls to INFORM, and a retried call is a retry, not a failure.

    python tests/test_source_pacing.py      # no network (urlopen is faked), no cost

    1. spacing    -- a paced host's next call starts no sooner than SPACING_S
                     after the previous one ended; other hosts are not paced
    2. serial     -- two threads never call a paced host at the same time
    3. recovered  -- dropped once, answered on the retry: one RETRY, no failure
    4. failed     -- dropped on every attempt: a source failure, raised
    5. split      -- the run record separates failures from retries
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import observability  # noqa: E402
from core.workflows import _fetch as F  # noqa: E402

results: list[tuple[str, str]] = []
PACED = "https://drmkc.jrc.ec.europa.eu/Inform-Index/API/InformAPI/x"
FREE = "https://api.worldbank.org/v2/x"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


class Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def fake(script):
    """urlopen that raises or answers in the order given, logging call times."""
    calls = []

    def urlopen(req, timeout=None, context=None):
        calls.append((time.monotonic(), threading.get_ident()))
        step = script.pop(0) if script else "ok"
        if step == "drop":
            raise ConnectionResetError("RemoteDisconnected")
        time.sleep(0.05)
        return Resp()

    return urlopen, calls


def main() -> int:
    real_open, real_spacing = F.urllib.request.urlopen, dict(F.SPACING_S)
    F.SPACING_S["drmkc.jrc.ec.europa.eu"] = 0.3
    F._LAST_END.clear()
    try:
        # 1. spacing
        F.urllib.request.urlopen, calls = fake([])
        for _ in range(3):
            F.get_bytes(PACED)
        gaps = [b[0] - a[0] for a, b in zip(calls, calls[1:])]
        check("spacing: paced host waits", all(g >= 0.3 for g in gaps), str([round(g, 2) for g in gaps]))
        F.urllib.request.urlopen, calls = fake([])
        t0 = time.monotonic()
        for _ in range(3):
            F.get_bytes(FREE)
        check("spacing: other hosts are not paced", time.monotonic() - t0 < 0.3)

        # 2. serial across threads
        F.urllib.request.urlopen, calls = fake([])
        ts = [threading.Thread(target=F.get_bytes, args=(PACED,)) for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        times = sorted(c[0] for c in calls)
        check("serial: threads take turns", all(b - a >= 0.3 for a, b in zip(times, times[1:])),
              str([round(b - a, 2) for a, b in zip(times, times[1:])]))

        # 3. recovered
        F.urllib.request.urlopen, calls = fake(["drop", "ok"])
        with observability.tool_scope() as scope:
            F.get_bytes(PACED, pause=0.0)
        fails, retries = observability.split_failures(list(scope))
        check("recovered: a retry, not a failure", not fails and len(retries) == 1, str(scope))

        # 4. failed
        F.urllib.request.urlopen, calls = fake(["drop", "drop"])
        raised = False
        with observability.tool_scope() as scope:
            try:
                F.get_bytes(PACED, pause=0.0)
            except ConnectionResetError:
                raised = True
        fails, retries = observability.split_failures(list(scope))
        check("failed: every attempt dropped is a failure", raised and len(fails) == 2 and not retries,
              str(scope))

        # 4b. Overpass (where_am_i): a request no attempt answered is recorded
        # as a source failure, also from the worker threads of the count step
        from core.workflows import osm_context as O
        from core import places

        real_pause, real_osm_open = places.BUSY_PAUSE, O.urllib.request.urlopen
        real_slot, real_pauses = O._slot_wait, O.RETRY_PAUSES
        places.BUSY_PAUSE = 0.0
        # The status check and the backoff are tested in test_where_am_i;
        # here only the attempts are counted.
        O._slot_wait, O.RETRY_PAUSES = (lambda url: None), (0.0, 0.0, 0.0)
        O.urllib.request.urlopen, _ = fake(["drop", "drop", "drop"])
        try:
            with observability.tool_scope() as scope:
                try:
                    O._overpass("[out:json];")
                except RuntimeError:
                    pass
            fails, _r = observability.split_failures(list(scope))
            check("overpass: a failed request is a source failure", len(fails) == 3, str(scope))
            O.urllib.request.urlopen, _ = fake(["drop", "ok"])
            import concurrent.futures
            import contextvars
            with observability.tool_scope() as scope:
                parent = contextvars.copy_context()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    list(pool.map(lambda q: parent.copy().run(O._overpass, q), ["[out:json];"]))
            fails, retries = observability.split_failures(list(scope))
            check("overpass: recorded from a worker thread, as a retry", not fails and len(retries) == 1,
                  str(scope))
        finally:
            places.BUSY_PAUSE, O.urllib.request.urlopen = real_pause, real_osm_open
            O._slot_wait, O.RETRY_PAUSES = real_slot, real_pauses

        # 5. split
        f, r = observability.split_failures([{"domain": "a"}, {"domain": "b", "recovered": True}])
        check("split: failures and retries apart", f == [{"domain": "a"}] and r[0]["domain"] == "b")
    finally:
        F.urllib.request.urlopen = real_open
        F.SPACING_S.clear()
        F.SPACING_S.update(real_spacing)
        F._LAST_END.clear()

    failed = [n for s, n in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
