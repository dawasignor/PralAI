"""RED metrics and the event stream: counted, timed, written, redacted.

    python tests/test_observability.py      # no model, no network, no cost

Every tool call and pipeline run is counted and
timed in process and appended to `runs/<run_id>/metrics.jsonl`; every event
goes to `events.jsonl` and any subscriber. Nothing written may carry a query
string, a key, a local path or a prompt. No HTTP endpoint exists yet (E-1).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cache_policy, logbook, observability     # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def test_redaction() -> None:
    r = observability.redact
    url = r("GET https://archive-api.open-meteo.com/v1/archive?latitude=6.9&apikey=abc")
    check("a URL becomes its domain; no path, no query string",
          url == "GET archive-api.open-meteo.com", url)
    win = r(r"wrote D:\My work\PralAI\runs\x\a.tif ok")
    check("a Windows path becomes <path>", win == "wrote <path> ok", win)
    posix = r("read /home/user/data/tile.tif done")
    check("a POSIX path becomes <path>", posix == "read <path> done", posix)
    keys = r("api_key=sk-abcdef1234567890 Authorization: Bearer xyz.token")
    check("keys and tokens are redacted", "sk-abc" not in keys and "xyz.token" not in keys
          and "abcdef" not in keys, keys)
    plain = r("TOPSIS ranked 5 products, 0.6535")
    check("ordinary text is left alone", plain == "TOPSIS ranked 5 products, 0.6535")
    check("domain() drops port and credentials",
          observability.domain("https://user:pw@overpass-api.de:443/api?x=1")
          == "overpass-api.de")


def test_red_metrics(tmp: Path) -> None:
    calls = observability.sample("pralai_tool_calls_total", tool="t_obs", status="success")
    count = observability.sample("pralai_tool_duration_seconds_count", tool="t_obs")
    with cache_policy.use("off", "obs-run"):
        observability.record_tool("t_obs", "success", 1.25, attempt=2)
        observability.record_tool("t_obs", "error", 0.5, error_type="runtime_error",
                                  source_failures=1)
        observability.record_pipeline("flood_analyst", "success", 12.0,
                                      note="see https://x.org/a?b=c")
    check("the call counter counts by tool and status",
          observability.sample("pralai_tool_calls_total", tool="t_obs",
                               status="success") == calls + 1
          and observability.sample("pralai_tool_calls_total", tool="t_obs",
                                   status="error") >= 1)
    check("the duration histogram observes every call",
          observability.sample("pralai_tool_duration_seconds_count",
                               tool="t_obs") == count + 2)
    check("the pipeline counter counts too",
          observability.sample("pralai_pipeline_runs_total", pipeline="flood_analyst",
                               status="success") >= 1)
    lines = [json.loads(x) for x in
             (tmp / "obs-run" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    check("each observation is one JSON line in runs/<run_id>/metrics.jsonl",
          len(lines) == 3 and lines[0]["tool"] == "t_obs" and lines[0]["attempt"] == 2
          and lines[1]["error_type"] == "runtime_error" and lines[2]["kind"] == "pipeline")
    check("each line carries the run id and the cache policy",
          all(x["run_id"] == "obs-run" and x["cache"] == "off" for x in lines))
    check("string extras on a pipeline line are redacted",
          lines[2]["note"] == "see x.org", lines[2]["note"])
    observability.record_tool("t_obs", "success", 0.1)
    check("outside a run nothing is written, and nothing fails",
          not (tmp / "").joinpath("metrics.jsonl").exists())


def test_events(tmp: Path) -> None:
    sink: list[dict] = []
    with cache_policy.use("on", "obs-events"), logbook.collect_events(sink):
        logbook.event("tool-start", tool="where_am_i", attempt=1)
        logbook.event("source-call", domain="overpass-api.de", ok=False, status=502,
                      reason=r"reply saved to C:\tmp\x.json from https://a.b/c?q=1")
    check("events reach a subscriber in order",
          [e["event"] for e in sink] == ["tool-start", "source-call"])
    reason = sink[1]["reason"]
    check("event strings are redacted on the way in",
          "<path>" in reason and "a.b" in reason and "q=1" not in reason, reason)
    on_disk = (tmp / "obs-events" / "events.jsonl").read_text(encoding="utf-8")
    check("events are written to runs/<run_id>/events.jsonl",
          on_disk.count("\n") == 2 and "q=1" not in on_disk)
    try:
        logbook.event("tool-exploded")
        check("an unknown event kind is refused", False)
    except ValueError:
        check("an unknown event kind is refused", True)

    with observability.tool_scope() as failures:
        observability.source_call("https://archive-api.open-meteo.com/v1/archive?lat=1",
                                  ok=False, status=400, reason="end_date out of range")
        observability.source_call("https://archive-api.open-meteo.com/v1/archive?lat=2")
    check("only failed source calls are collected, by domain",
          failures == [{"domain": "archive-api.open-meteo.com", "status": 400,
                        "reason": "end_date out of range"}], str(failures))
    observability.source_call("https://x.org/?y=1", ok=False)
    check("a source call outside any tool scope does not fail", True)


def main() -> None:
    saved = (cache_policy.RUNS_DIR, logbook._log_path)
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        cache_policy.RUNS_DIR = tmp
        logbook._log_path = lambda: None
        try:
            check("prometheus-client is installed (D-18)",
                  observability.REGISTRY is not None)
            test_redaction()
            test_red_metrics(tmp)
            test_events(tmp)
        finally:
            cache_policy.RUNS_DIR, logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
