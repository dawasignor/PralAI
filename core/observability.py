"""RED metrics per tool call and per pipeline, in process.

Rate, errors and duration for every tool call and every pipeline run:

    pralai_tool_calls_total{tool,status}          counter
    pralai_tool_duration_seconds{tool}            histogram
    pralai_pipeline_runs_total{pipeline,status}   counter
    pralai_pipeline_duration_seconds{pipeline}    histogram
    pralai_agent_tokens_total{agent,kind}         counter (input / output)

held in this module's own `prometheus_client` registry, and every observation
also appended as one JSON line to `runs/<run_id>/metrics.jsonl`. **Nothing is
exposed over HTTP yet** -- the `/metrics` endpoint, Prometheus and Grafana are
E-1. The JSONL is what an RQ5 batch reads for per-run duration and failures.

A separate registry rather than the global default, so a test can read exact
counts and nothing registered by a library leaks into the sample.

**`source_failures` are kept apart from a different answer.** A tool that got
an Overpass 502 and returned less is not the same as a tool that returned a
different number from the same data, and RQ5 has to tell them apart. A tool
reports a failed upstream call with `source_call(url, ok=False, ...)`; the
contract wrapper in core/monty_graph.py copies the list into the payload's
`run.source_failures`.

Redaction (applies to everything written here and to every event): a URL is
reduced to its domain, a local path to `<path>`, and anything shaped like a
key or token to `<redacted>`. Prompts are never passed in at all.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from . import cache_policy

try:  # D-18: prometheus-client is a dependency; the JSONL half needs nothing.
    from prometheus_client import CollectorRegistry, Counter, Histogram
except ImportError:  # pragma: no cover - reported, never fatal
    CollectorRegistry = None  # type: ignore[assignment]

_BUCKETS = (0.05, 0.25, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800)

if CollectorRegistry is not None:
    REGISTRY = CollectorRegistry(auto_describe=True)
    TOOL_CALLS = Counter("pralai_tool_calls_total", "Tool calls by outcome.",
                         ["tool", "status"], registry=REGISTRY)
    TOOL_DURATION = Histogram("pralai_tool_duration_seconds",
                              "Tool call duration.", ["tool"],
                              buckets=_BUCKETS, registry=REGISTRY)
    PIPELINE_RUNS = Counter("pralai_pipeline_runs_total",
                            "Pipeline runs by outcome.",
                            ["pipeline", "status"], registry=REGISTRY)
    PIPELINE_DURATION = Histogram("pralai_pipeline_duration_seconds",
                                  "Pipeline run duration.", ["pipeline"],
                                  buckets=_BUCKETS, registry=REGISTRY)
    AGENT_TOKENS = Counter("pralai_agent_tokens_total", "Model tokens by agent.",
                           ["agent", "kind"], registry=REGISTRY)
else:  # pragma: no cover
    REGISTRY = None


# -- redaction ---------------------------------------------------------------
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+", re.I)
# A segment may contain single spaces ("D:\My work\..."), as long as a
# separator follows; the last segment stops at the first space.
_WIN_PATH = re.compile(
    r"\b[A-Za-z]:[\\/](?:[^\s'\"<>\\/]+(?: [^\s'\"<>\\/]+)*[\\/])*[^\s'\"<>]*")
_POSIX_PATH = re.compile(r"(?<![\w:/.])/(?:[\w.-]+/)+[\w.-]*")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;'\"]+")
_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|authorization)"
    r"(\s*[:=]\s*)(?!<redacted>)[^\s,;'\"]+"
    r"|\bsk-[A-Za-z0-9_-]{8,}")


def domain(url: str) -> str:
    """The host of a URL, nothing else: no path, no query string, no port."""
    try:
        return (urlsplit(url).hostname or "").lower() or "<url>"
    except ValueError:
        return "<url>"


def redact(text: Any) -> str:
    """Text safe to log or send to a viewer: URLs to domains, no paths or keys."""
    out = str(text)
    # URLs first: a key inside a query string goes with the query string.
    out = _URL.sub(lambda m: domain(m.group(0)), out)
    out = _BEARER.sub("Bearer <redacted>", out)
    out = _SECRET.sub(lambda m: (m.group(1) + m.group(2) if m.group(1) else "")
                      + "<redacted>", out)
    out = _WIN_PATH.sub("<path>", out)
    out = _POSIX_PATH.sub("<path>", out)
    return out


# -- the per-call scope ------------------------------------------------------
# The source failures of the tool call in progress. A ContextVar, as for the
# cache policy: each console run has its own thread.
_SCOPE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "observability_scope", default=None)


@contextmanager
def tool_scope():
    """Collect the `source_call` failures made inside one tool call."""
    failures: list[dict[str, Any]] = []
    token = _SCOPE.set(failures)
    try:
        yield failures
    finally:
        _SCOPE.reset(token)


def source_call(url: str, *, ok: bool = True, status: int | None = None,
                reason: str = "", duration_s: float | None = None,
                record: bool = True) -> dict[str, Any]:
    """Record one upstream request. Domain only -- never the query string.

    `record=False` logs the event but leaves the failure out of the tool's
    scope for now: the caller is about to retry, and settles it with
    `settle_attempts` once it knows whether the retry worked."""
    from .logbook import event

    host = domain(url)
    fields: dict[str, Any] = {"domain": host, "ok": ok}
    if status is not None:
        fields["status"] = status
    if duration_s is not None:
        fields["duration_s"] = round(duration_s, 3)
    if reason:
        fields["reason"] = redact(reason)[:120]
    event("source-call", **fields)
    entry = {k: v for k, v in fields.items() if k != "ok"}
    if not ok and record:
        scope = _SCOPE.get()
        if scope is not None:
            scope.append(entry)
    return entry


def settle_attempts(failed: list[dict[str, Any]], *, recovered: bool) -> None:
    """The failed attempts of one request, once its outcome is known. A
    request that a retry answered is a RETRY (`recovered`), not a source
    failure: the tool got its data, and `core/compare.py` must not set the
    fact aside for it. A request whose last attempt failed is a failure."""
    scope = _SCOPE.get()
    if scope is None:
        return
    for f in failed:
        scope.append({**f, "recovered": True} if recovered else f)


def split_failures(entries: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """(source failures, retries that recovered) from one tool scope."""
    return ([e for e in entries if not e.get("recovered")],
            [e for e in entries if e.get("recovered")])


# -- recording ---------------------------------------------------------------
def _append_jsonl(record: dict[str, Any]) -> None:
    folder = cache_policy.run_dir()
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "metrics.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        # Metrics are a record of the run, not a part of it: a full disk must
        # cost the metrics line, never the analysis.
        pass


def _stamp() -> str:
    # The same form as logbook.event, so the two JSON logs of a run sort and
    # join on one clock: local time with its offset.
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def record_tool(tool: str, status: str, duration_s: float, *,
                attempt: int = 1, error_type: str = "",
                source_failures: int = 0) -> None:
    """One finished tool call: counter, histogram and a JSONL line."""
    if REGISTRY is not None:
        TOOL_CALLS.labels(tool=tool, status=status).inc()
        TOOL_DURATION.labels(tool=tool).observe(duration_s)
    _append_jsonl({"kind": "tool", "at": _stamp(), "run_id": cache_policy.run_id(),
                   "cache": cache_policy.current(), "tool": tool, "status": status,
                   "duration_s": round(duration_s, 3), "attempt": attempt,
                   "error_type": error_type, "source_failures": source_failures})


def record_pipeline(pipeline: str, status: str, duration_s: float, **extra: Any) -> None:
    """One finished pipeline run (core/pipelines/, CP8)."""
    if REGISTRY is not None:
        PIPELINE_RUNS.labels(pipeline=pipeline, status=status).inc()
        PIPELINE_DURATION.labels(pipeline=pipeline).observe(duration_s)
    _append_jsonl({"kind": "pipeline", "at": _stamp(),
                   "run_id": cache_policy.run_id(), "cache": cache_policy.current(),
                   "pipeline": pipeline, "status": status,
                   "duration_s": round(duration_s, 3),
                   **{k: redact(v) if isinstance(v, str) else v
                      for k, v in extra.items()}})


def record_agent(agent: str, duration_s: float, *, input_tokens: int = 0,
                 output_tokens: int = 0, cost_usd: float | None = None,
                 turns: int = 1, truncated: bool = False,
                 cache_read: int = 0, prompt_sha: str = "",
                 overlay_sha: str = "", model: str = "",
                 providers: list[str] | None = None,
                 settings: dict | None = None) -> None:
    """One finished agent call: its tokens and, when the provider reported
    it, its cost. The RQ5 runner sums these per run (scripts/run_n.py)."""
    if REGISTRY is not None:
        AGENT_TOKENS.labels(agent=agent, kind="input").inc(input_tokens)
        AGENT_TOKENS.labels(agent=agent, kind="output").inc(output_tokens)
    _append_jsonl({"kind": "agent", "at": _stamp(), "run_id": cache_policy.run_id(),
                   "cache": cache_policy.current(), "agent": agent,
                   "duration_s": round(duration_s, 3), "turns": turns,
                   "input_tokens": input_tokens, "output_tokens": output_tokens,
                   "cache_read": cache_read, "cost_usd": cost_usd, "truncated": truncated,
                   "prompt_sha": prompt_sha, "overlay_sha": overlay_sha,
                   "model": model, "providers": list(providers or []),
                   "settings": dict(settings or {})})


def sample(metric: str, **labels: str) -> float:
    """The current value of one series, for tests and the RQ5 runner."""
    if REGISTRY is None:
        return 0.0
    value = REGISTRY.get_sample_value(metric, labels)
    return float(value or 0.0)


class Timer:
    """`with Timer() as t: ...; t.seconds` -- monotonic, so a clock change
    mid-call cannot produce a negative duration."""

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc: Any) -> None:
        self.seconds = time.perf_counter() - self._t0
