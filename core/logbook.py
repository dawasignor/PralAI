"""Concise exchange log.

One line per exchange, so a whole run reads as a transcript:

    16:15:00  argos -> monty    delegate       attempt=1 event_id=42
    16:15:00  monty .  receive_input           params={'event_id': 42}
    16:15:00  monty -> mcp:M    fetch_code     attempt=1
    16:15:01  mcp:M -> monty    snippet        41 chars entrypoint=run
    16:15:01  monty .  execute_code            sandbox=subprocess timeout=30s
    16:15:01  monty -> argos    ERROR          signature_mismatch: run() got ...
    16:15:01  argos .  debug                   snippet expects 'val'; params carry 'event_id'
    16:15:01  argos -> monty    retry          attempt=2
    16:15:02  monty -> argos    OK             output=84

Every helper returns the formatted line so a node can also push it into the
graph state -- the log lives in three places on purpose: stdout (watching a
run), `logs/exchange.log` (after the fact), and the state channel (what the
agents themselves can see).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT

_MAX_DETAIL = 160

# A live subscriber to this run's lines.
#
# The graph hands its exchange log back only when the whole run ends, which is
# a minute of silence for anyone watching. Every line already passes through
# `log()`, so a sink here is the one place that can report progress without a
# single node knowing it is being watched.
#
# A ContextVar and not a global: the HTTP server runs each task in its own
# thread, and one run must never collect another run's lines.
_SINK: ContextVar[list[str] | None] = ContextVar("logbook_sink", default=None)


@contextmanager
def collect(sink: list[str]):
    """Append every line logged inside this block to `sink`, as it happens."""
    token = _SINK.set(sink)
    try:
        yield sink
    finally:
        _SINK.reset(token)


def _log_path() -> Path | None:
    raw = (os.getenv("EXCHANGE_LOG") or "logs/exchange.log").strip()
    if raw.lower() in ("", "0", "off", "none"):
        return None
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _echo() -> bool:
    return (os.getenv("EXCHANGE_LOG_ECHO") or "1").strip() == "1"


def log(src: str, dst: str | None, action: str, detail: str = "") -> str:
    """Record one exchange. `dst=None` means an internal step, rendered as `.`."""
    detail = " ".join(str(detail).split())
    if len(detail) > _MAX_DETAIL:
        detail = detail[: _MAX_DETAIL - 3] + "..."
    arrow = f"-> {dst}" if dst else ".  "
    line = (
        f"{datetime.now().strftime('%H:%M:%S')}  "
        f"{src:<6} {arrow:<10} {action:<14} {detail}".rstrip()
    )

    # The live subscriber first: a watcher should see a step at the moment it
    # happens, not after stdout and the file have been written.
    sink = _SINK.get()
    if sink is not None:
        sink.append(line)

    if _echo():
        print(line, flush=True)
    path = _log_path()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return line


# Structured events: tool-start, tool-end, source-call,
# agent-call, pipeline-step, fallback-used, cache-state. One stream with three
# consumers -- the RED recorder (core/observability.py), the JSON log beside
# the run, and later the console's steps panel (E-2), which subscribes here.
EVENTS = ("tool-start", "tool-end", "tool-step", "source-call", "agent-call",
          "pipeline-step", "fallback-used", "cache-state")

_EVENT_SINK: ContextVar[list[dict] | None] = ContextVar("event_sink", default=None)


@contextmanager
def collect_events(sink: list[dict]):
    """Append every structured event emitted inside this block to `sink`."""
    token = _EVENT_SINK.set(sink)
    try:
        yield sink
    finally:
        _EVENT_SINK.reset(token)


def event(kind: str, src: str = "event", **fields) -> dict:
    """Emit one structured event: a log line, a JSON line, and the sink.

    Every string value is redacted on the way in (observability.redact): a
    URL becomes its domain, a local path `<path>`, a key `<redacted>`. Callers
    never pass a prompt. An unknown kind is refused, because a consumer
    filtering on a fixed vocabulary would drop it silently.
    """
    from . import cache_policy
    from .observability import redact

    if kind not in EVENTS:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {EVENTS}")
    clean = {k: (redact(v) if isinstance(v, str) else v) for k, v in fields.items()}
    record = {"event": kind,
              "at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
              "run_id": cache_policy.run_id(), **clean}

    sink = _EVENT_SINK.get()
    if sink is not None:
        sink.append(record)
    log(src, None, kind, " ".join(f"{k}={v}" for k, v in clean.items()))

    folder = cache_policy.run_dir()
    if folder is not None:
        try:
            import json

            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass                      # the event log never fails the run
    return record


def banner(task: str) -> str:
    """Mark the start of a run so a shared log file stays readable."""
    return log("run", None, "start", task)
