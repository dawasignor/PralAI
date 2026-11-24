"""HTTP endpoint for the Argos Console front end.

    python -m api.console_server          # serves http://127.0.0.1:8787

Seven routes:

    POST /api/execute_node       start a run; returns a taskId immediately
    POST /api/session/open       the pinned session (core/pinned.py), with
                                 its transcript (core/transcript.py)
    GET  /api/task/<taskId>      how it is going: elapsed, steps so far, answer
    POST /api/execute_node_sync  run to completion in one request
    GET  /api/workflows          the workflow registry
    GET  /api/figure/<name>      one experiment figure (PNG), by the name in an
                                 answer's `<image - region-year-figure>`
    GET  /api/thesis_path        the thesis outputs and their status for
                                 `?sessionId=` (core/thesis_path.py)

The console uses the first two. A run takes between twenty seconds and two
minutes, and a blocking request tells the caller nothing for the whole of it --
so the work happens on a thread and the console asks how it is going, which is
also what lets the session log fill as the run happens.

The shape returned when a run finishes is the contract `src/api/types.ts`
already describes. The console's Netlify function is a stub of exactly that
shape and answers in one hop with no `taskId`, so the client handles both: a
reply means done, a taskId means poll.

Stdlib only. The repo keeps its dependency list short on purpose, and a local
dev endpoint does not earn a web framework.

**Local dev only.** It binds loopback, has no authentication and no rate limit,
and it runs a graph that spends money per request. Do not expose it.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from core import figure_refs, pinned, redact, thesis_path, transcript, workflows
from core.graph import run_task

HOST, PORT = "127.0.0.1", 8787
MAX_BODY = 256 * 1024

# The console's five agents. Anything else in a log line is machinery, not an
# agent, and is attributed to argos so the UI can still colour the row.
AGENTS = {"argos", "norwood", "monty", "entro", "citadel"}

# The planner is Chanakya on screen and `citadel` in the code (agents/citadel).
# Renamed here, at the seam, so the console never sees the old name and the
# backend's folders, flow cards and memory do not have to move.
UI_SLUG = {"citadel": "chanakya"}


def _ui(slug: str) -> str:
    return UI_SLUG.get(slug, slug)

def _workflow_names() -> set[str]:
    """Registered workflow names, read from the registry rather than copied.

    This was a hardcoded set of four and had already gone stale twice --
    `fetch_place_context` and `fetch_weather_grid` were both registered and
    both invisible here, so a log line naming one was never tagged with it.
    Read live, because a name added in Python is a name this should know.
    """
    try:
        return set(workflows.names(include_inactive=True))
    except Exception:                                       # noqa: BLE001
        return set()

# `HH:MM:SS  src -> dst   action   detail`, the format core/logbook.py writes.
LOG_LINE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<src>\S+)\s+"
    r"(?:->\s*(?P<dst>\S+)|\.)\s+"
    r"(?P<action>\S+)\s*"
    r"(?P<detail>.*)$"
)

# What an action means to a reader, in plain English. The log is written for a
# developer watching stdout; the console's session log is written for someone
# who will not read the code, so the two are not the same sentence.
PHRASING = {
    "start": "Session started.",
    "session-carry": "Carrying the area and time frame forward from earlier "
                     "in this session.",
    "geocode": "Looked the place up in the OpenStreetMap gazetteer.",
    "aoi": "Area of interest settled, with its bounding box.",
    "aoi-confirmed": "You confirmed the area of interest.",
    "aoi-ambiguous": "That place name matches more than one location; asking "
                     "which one.",
    "aoi-missing": "No area of interest named; answering without one.",
    "window-missing": "No dates named; answering without a time window.",
    "event-records": "Checked the disaster registers for this event.",
    "figures": "Registered this run's rasters so the answer can show them.",
    "figures-failed": "This run's rasters could not be registered; the answer "
                      "will have no images.",
    "aoi-stated": "Using the bounding box you supplied, exactly as given.",
    "window-stated": "Using the dates you supplied, exactly as given.",
    "cached": "Saved the result for this session.",
    "cache-failed": "The result could not be saved for this session; the "
                    "analysis itself is unaffected.",
    "deferred": "Held back the other workflows; one runs per turn.",
    # The register is switched off (places.GDACS_ENABLED). These stay so an
    # older log still renders, and so turning it back on needs no edit here.
    "event-search": "Checked the GDACS disaster register for a matching event.",
    "window": "Time frame settled against a recorded event.",
    "window-confirmed": "You confirmed the time frame.",
    "window-ambiguous": "More than one recorded event covers that period; "
                        "asking which one.",
    "window-unconfirmed": "The disaster register could not corroborate that "
                          "window; using the dates you gave, marked "
                          "unconfirmed.",
    "resolve-failed": "Could not reach the gazetteer or the disaster "
                      "register; continuing without a confirmed area.",
    "resolve-unreadable": "Could not read the area and time frame out of that "
                          "message.",
    "resolve-retry": "Reading that message again.",
    "delegate": "Argos routed the request to {dst}.",
    "finish": "Argos assembled the final answer.",
    "flow": "Running the {first} analysis.",
    "flow-unknown": "Argos named an analysis that does not exist; "
                    "running the general path instead.",
    "catalogue-partial": "The workflow catalogue could not be read from the "
                         "vault; routing on function signatures alone.",
    "finding": "{src} returned its finding.",
    "receive_input": "Monty received the task list.",
    "resolve_tasks": "Monty resolved every task name against the registry.",
    "run": "Running {first}.",
    "ok": "{first} returned successfully.",
    "OK": "All tasks completed.",
    "FAILED": "{first} failed.",
    "ERROR": "Monty could not run the batch.",
    "PARTIAL": "Some tasks failed; the rest completed.",
    "debug": "Argos is diagnosing the failure.",
    "retry": "Argos patched the task list and re-sent it.",
    "abandon": "Argos stopped: the task list cannot fix this failure.",
    "patch-noop": "The patch is identical to the list that just failed.",
    "patch-contradiction": "The patch claimed to drop a task but dropped none.",
    "give-up": "Retries exhausted. Reporting the failure.",
    "truncated": "{src} ran out of room and stopped mid-sentence; the reply is incomplete.",
    "hand-back": "A specialist answered, so Argos is replying rather than "
                 "delegating again.",
    "suggest": "Argos suggests what to look at next.",
    "suggest-path": "Argos offered the next step on the thesis path.",
    "facts": "Updated the session fact sheet from the stored results.",
    "reused": "Used the result kept earlier in this session; nothing was "
              "fetched again.",
    "aoi-pinned": "This session's area and time frame are fixed; the change "
                  "was not made.",
    "plain-language": "Replaced internal names in the answer with plain ones.",
}

LEVELS = {
    "FAILED": "error", "ERROR": "error", "give-up": "error", "abandon": "error",
    "PARTIAL": "warn", "patch-noop": "warn", "patch-contradiction": "warn",
    "flow-unknown": "warn", "catalogue-partial": "warn",
    "resolve-failed": "warn", "window-unconfirmed": "warn",
    "cache-failed": "warn", "cached": "ok",
    "aoi-stated": "ok", "window-stated": "ok", "deferred": "info",
    "resolve-unreadable": "warn", "resolve-retry": "warn",
    "window-missing": "info", "event-records": "ok",
    "figures": "ok", "figures-failed": "warn",
    "truncated": "error",
    "aoi-ambiguous": "warn", "window-ambiguous": "warn", "aoi-missing": "info",
    "ok": "ok", "OK": "ok", "finding": "ok", "finish": "ok",
    "aoi": "ok", "window": "ok",
    "aoi-confirmed": "ok", "window-confirmed": "ok",
    "suggest": "info", "hand-back": "info", "plain-language": "warn",
    "reused": "ok", "aoi-pinned": "warn",
}


def _iso(clock: str) -> str:
    """A log line carries HH:MM:SS; the console wants ISO 8601."""
    today = datetime.now(timezone.utc).date().isoformat()
    return f"{today}T{clock}Z"


def _to_event(line: str, index: int) -> dict[str, Any] | None:
    """One exchange-log line -> one LogEvent the console can render."""
    match = LOG_LINE.match(line.strip())
    if not match:
        return None
    src, dst = match["src"], match["dst"] or ""
    action, detail = match["action"], (match["detail"] or "").strip()

    # The detail usually opens with the thing acted on -- a workflow name, a
    # flow name, a result id. That first token is what a reader wants named.
    first = detail.split()[0].rstrip(":,") if detail else action
    workflow = next((w for w in _workflow_names() if w in detail), None)

    template = PHRASING.get(action, "{src} {action}.")
    return {
        "id": f"ev-{index}-{uuid.uuid4().hex[:8]}",
        "ts": _iso(match["time"]),
        "level": LEVELS.get(action, "info"),
        "agent": _ui(src) if src in AGENTS else "argos",
        # Who takes over because of this line. A delegation is logged by the
        # agent handing off, so attributing it to `src` alone lights up Argos
        # while Norwood is the one actually working. The console follows this
        # when it is set.
        **({"routedTo": _ui(dst)} if dst in AGENTS else {}),
        **({"workflow": workflow} if workflow else {}),
        "message": template.format(src=_ui(src), dst=_ui(dst) or "a specialist",
                                   action=action, first=first),
        "detail": detail or None,
    }


def _status(state: dict[str, Any]) -> str:
    """How the run ended -- judged on where it ended up, not how it got there.

    The retry loop exists so a first attempt is allowed to fail. Scanning the
    whole transcript for "FAILED" counts superseded attempts as damage and
    reports a run that recovered 4/4 on attempt two as degraded, which trains a
    reader to ignore the badge. `monty_result` is the last word on the last
    attempt; only that decides.
    """
    # A run that gave up carries its technical report; the answer is now one
    # plain sentence, so it is not what decides. The old wording is still
    # recognised, for a state from before the change.
    answer = state.get("answer") or ""
    if state.get("failure_report") or answer.startswith("FAILED after"):
        return "error"

    result = state.get("monty_result") or {}
    tasks = result.get("results") or {}
    if tasks and any(t.get("status") != "success" for t in tasks.values()):
        return "partial"
    return "ok"


def _finished(request: dict[str, Any], session_id: str, state: dict[str, Any],
              lines: list[str] | None = None) -> dict[str, Any]:
    """A completed run, in the shape the console renders.

    `lines` is the live record collected while the run happened, and it wins
    when there is one. A poller has already been shown those events; rebuilding
    the list from `exchange_log` on the last poll would hand it a different set
    in a different order, and the session log would reshuffle at the moment the
    answer arrived. The sink is also the fuller record -- it catches every line
    logged, including the ones a node never pushed into state.
    """
    source = lines if lines is not None else (state.get("exchange_log") or [])
    events = [event for i, line in enumerate(source)
              if (event := _to_event(line, i))]
    # The technical half of a failure: the answer says one plain sentence,
    # and the session log -- the developer's view -- carries the rest.
    if report := state.get("failure_report"):
        events.append({
            "id": f"e-{len(events)}-failure",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": "error",
            "agent": "argos",
            # The traceback stays in the server's own log; the reader is told
            # only that it exists (user bug, 2026-11-02: no dev items shown).
            "message": "The technical details of the failure are kept in the "
                       "server's log for a developer.",
            "detail": None,
        })

    # flows_run records what actually ran; `flow` is only ever the next hop and
    # is empty by the time the run ends.
    flows = [entry.split(":", 1)[-1] for entry in state.get("flows_run") or []]

    return {
        "sessionId": session_id,
        "node": request.get("node") or "argos",
        "status": _status(state),
        "reply": state.get("answer") or "(the run produced no answer)",
        "events": events,
        "state": {
            "route": _ui(state.get("route") or "argos"),
            "steps": state.get("steps", 0),
            "workflowsRun": sorted(
                {w for line in (state.get("exchange_log") or [])
                 for w in _workflow_names() if w in line}),
            "flowsRun": flows,
        },
        # What Argos suggests doing next, offered rather than run. Empty when
        # the question is answered and nothing obvious follows.
        "nextStep": state.get("next_step") or None,
        # The disaster-register lookup, structured, for a console that wants
        # real links. The same records are already in `reply` as text.
        "eventRecords": state.get("event_records") or None,
        # The constants, for the console to show. The user is the one who can
        # tell at a glance that the AOI is the wrong Boston, so the resolved
        # values belong on screen and not only in the prompt.
        "session": _session_view(state.get("session")),
        # The thesis outputs and how far this session is, for the flowchart.
        # Read from the record after the run, so this turn's fetch counts.
        "thesisPath": thesis_path.view(state.get("session")),
    }


def execute_node(request: dict[str, Any]) -> dict[str, Any]:
    """Run one task to completion, synchronously.

    Kept because it is the plain way to call the graph from a script or a test.
    The console uses `start_task` / `task_status` instead, so it can show the
    run happening rather than a spinner over a minute of silence.
    """
    task = ((request.get("input") or {}).get("task") or "").strip()
    if not task:
        raise ValueError("input.task is required")

    # The console has always sent a session id and this endpoint always echoed
    # it back unread, which is why every turn began with an empty state and no
    # memory of the AOI. Minting one when it is absent keeps a caller that
    # never sends one -- curl, a test -- from sharing a record with the next.
    session_id = str(request.get("sessionId") or f"s-{uuid.uuid4().hex[:8]}")
    return _tracked_run(request, session_id)


def _run(request: dict[str, Any], session_id: str) -> dict[str, Any]:
    """`run_task` with the request's settings -- the one call both entry
    points make, so they cannot drift apart."""
    return run_task(
        (request.get("input") or {})["task"].strip(),
        persona=str(request.get("persona") or ""),
        # Accepts the console's camelCase and the graph's snake_case, because
        # this is the seam between the two and neither should have to bend.
        verified_sources_only=bool(request.get("verifiedSourcesOnly")
                                   or request.get("verified_sources_only")),
        session_id=session_id,
        # Opt-in. A console question should not write into a thesis cache just
        # because one exists; naming the event is what says "this run is part
        # of that experiment". Forwarded by both entry points: a specialist
        # with no experiment cache does not fail, it reports that no payloads
        # exist -- a plumbing bug that came back as a finding.
        experiment=str(request.get("experiment")
                       or (request.get("input") or {}).get("experiment")
                       or ""),
        cache=_cache(request),
    )


def _tracked_run(request: dict[str, Any], session_id: str,
                 lines: list[str] | None = None) -> dict[str, Any]:
    """Run one turn; for the pinned session, add it to the transcript so the
    console can replay it (core/transcript.py)."""
    track = transcript.enabled(session_id)
    try:
        state = _run(request, session_id)
        payload = _finished(request, session_id, state, lines)
        flows = list(state.get("flows_run") or [])
    except Exception as exc:
        if not track:
            raise
        payload = _crashed(session_id, exc)
        flows = []
    if track:
        try:
            transcript.record_turn(
                session_id, task=(request.get("input") or {}).get("task", "").strip(),
                reply=payload.get("reply", ""), status=payload.get("status", "ok"),
                agent=(payload.get("state") or {}).get("route") or "argos",
                persona=str(request.get("persona") or ""), flows=flows,
                events=payload.get("events") or [])
        except Exception as exc:                            # noqa: BLE001
            # The answer stands; only its place in the replay is lost.
            print(f"[console-api] turn not recorded: {type(exc).__name__}: {exc}")
    return payload


def _crashed(session_id: str, exc: Exception, task_id: str = "run") -> dict[str, Any]:
    """A crashed run as an answer the console can show."""
    # The reply is for the reader, in plain language; the exception is for a
    # developer, in the session log.
    return {
        "sessionId": session_id, "node": "argos", "status": "error",
        "reply": "There was an error while working on this request, so it "
                 "has no answer. Please try again.",
        "events": [{
            "id": f"crash-{task_id}",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": "error", "agent": "argos",
            "message": "The run stopped with an error.",
            "detail": f"{type(exc).__name__}: {exc}",
        }],
        "session": None,
    }


def _running(session_id: str) -> bool:
    with _JOBS_LOCK:
        return any(job["sessionId"] == session_id and job["finishedAt"] is None
                   for job in _JOBS.values())


def open_session(request: dict[str, Any]) -> dict[str, Any]:
    """The pinned session, as the console opens it, with its transcript."""
    from core import session as _session

    sid = pinned.SESSION_ID
    record = _session.load(sid)
    _session.save(record)
    return {"sessionId": sid, "session": _session_view(record),
            "thesisPath": thesis_path.view(record),
            **transcript.view(sid), "running": _running(sid)}


def _cache(request: dict[str, Any]) -> str:
    """The run's cache policy from `cache` (true/false or on/off), default on.

    An unreadable value is refused rather than defaulted: a request that meant
    `off` and ran cached would measure the cache (core/cache_policy.py).
    """
    from core import cache_policy

    return cache_policy.parse(request.get("cache", True))


# ---------------------------------------------------------------------------
# Running a task in the background, so the console can watch it
# ---------------------------------------------------------------------------
# A run takes between twenty seconds and two minutes, and a blocking POST tells
# the caller nothing until it is over. These endpoints split it: start the work,
# then ask how it is going. The answer carries the steps taken so far, so the
# session log fills as the run happens rather than replaying afterwards.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

# How long a finished job stays readable. Long enough for a reload to still find
# its answer; short enough that a day of use does not hold every run in memory.
JOB_TTL_SECONDS = 15 * 60


def _prune_jobs(now: float) -> None:
    dead = [tid for tid, job in _JOBS.items()
            if job["finishedAt"] and now - job["finishedAt"] > JOB_TTL_SECONDS]
    for tid in dead:
        _JOBS.pop(tid, None)


def _run_job(task_id: str, request: dict[str, Any], session_id: str) -> None:
    """The worker. Collects log lines as they are written, then the result."""
    from core import logbook

    job = _JOBS[task_id]
    try:
        # Every exchange line passes through logbook.log, so subscribing there
        # reports progress without any node knowing it is being watched.
        with logbook.collect(job["lines"]):
            payload = _tracked_run(request, session_id, job["lines"])
    except Exception as exc:                                    # noqa: BLE001
        # A crashed run is an answer the console can show, not a poll that
        # never resolves.
        payload = _crashed(session_id, exc, task_id)
    with _JOBS_LOCK:
        job["result"] = payload
        job["finishedAt"] = time.time()


def start_task(request: dict[str, Any]) -> dict[str, Any]:
    """Begin a run and return at once with something to poll."""
    task = ((request.get("input") or {}).get("task") or "").strip()
    if not task:
        raise ValueError("input.task is required")
    session_id = str(request.get("sessionId") or f"s-{uuid.uuid4().hex[:8]}")

    task_id = f"t-{uuid.uuid4().hex[:12]}"
    now = time.time()
    with _JOBS_LOCK:
        _prune_jobs(now)
        _JOBS[task_id] = {"lines": [], "result": None, "startedAt": now,
                          "finishedAt": None, "sessionId": session_id}

    # Daemon: a run in flight must not keep the server alive at shutdown.
    threading.Thread(target=_run_job, args=(task_id, request, session_id),
                     daemon=True, name=f"task-{task_id}").start()
    return {"taskId": task_id, "sessionId": session_id, "status": "running",
            "elapsedMs": 0, "events": []}


def task_status(task_id: str) -> dict[str, Any]:
    """How a run is going: elapsed time, the steps so far, the answer if done.

    Events are rebuilt from every line seen so far rather than only the new
    ones. The list is tens of entries and the caller replaces its own copy, so
    an accounting of what it has already been shown would be state to keep in
    sync for no gain.
    """
    with _JOBS_LOCK:
        job = _JOBS.get(task_id)
        if job is None:
            # An unknown id is the honest answer: the server restarted, or the
            # job aged out. Either way the caller should stop polling.
            return {"taskId": task_id, "status": "unknown", "elapsedMs": 0,
                    "events": [],
                    "reply": "This request is no longer being followed, so "
                             "its answer cannot be shown. Please ask again."}
        lines = list(job["lines"])
        result = job["result"]
        started, finished = job["startedAt"], job["finishedAt"]

    elapsed_ms = int(((finished or time.time()) - started) * 1000)
    if result is not None:
        return {**result, "taskId": task_id, "elapsedMs": elapsed_ms}

    return {
        "taskId": task_id, "sessionId": job["sessionId"], "status": "running",
        "elapsedMs": elapsed_ms,
        "events": [e for i, line in enumerate(lines)
                   if (e := _to_event(line, i))],
    }


def _thesis_path_for(query: str) -> list[dict[str, Any]]:
    """The path for `?sessionId=...`, or the untouched path without one.

    Read-only: `session.load` returns a fresh record for an unknown id and
    writes nothing, so asking about a session cannot create one.
    """
    from urllib.parse import parse_qs

    from core import session

    sid = (parse_qs(query).get("sessionId") or [""])[0].strip()
    # Session ids name files under sessions/; anything else is not one.
    if sid and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
        sid = ""
    return thesis_path.view(session.load(sid) if sid else None)


def _session_view(record: Any) -> dict[str, Any] | None:
    """The session's constants, in the shape the console renders."""
    if record is None:
        return None
    return {
        "aoi": record.place.display or None,
        "bbox": list(record.place.bbox) if record.place.bbox else None,
        "window": record.window.line() or None,
        "event": record.window.event or None,
        "confirmed": record.confirmed,
        "turns": record.turns,
        "digest": record.digest or None,
        # Every data input fetched for this AOI and window, by tool name, so
        # "Check inputs" can turn its button green (user 2026-11-02).
        "fetched": sorted({workflows.canonical(n) for n in (record.workflows_run or [])}),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict[str, Any]) -> None:
        # Nothing of this machine reaches the reader: local paths and the
        # account name are replaced in every string sent (core/redact.py).
        body = json.dumps(redact.deep(payload), default=str).encode()
        self.send_response(code)
        # The agents answer with en-dashes, em-dashes and +- signs. JSON
        # defaults to UTF-8, but saying so costs nothing and removes the
        # one place a browser could guess wrong.
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The console runs on another port in dev, so the browser treats it as
        # cross-origin. Loopback only -- see the module docstring.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:                           # CORS preflight
        self._send(200, {"ok": True})

    def do_GET(self) -> None:
        path = self.path.rstrip("/")
        for prefix in ("/api/task/", "/task/"):
            if path.startswith(prefix):
                self._send(200, task_status(path[len(prefix):]))
                return
        # The console used to keep its own copy of this list in TypeScript, so
        # a function registered in Python stayed invisible in the navigator and
        # the flow graph until somebody remembered to edit a second file. It
        # went stale three times. The registry is the only truth about what
        # Monty can run, so it is what the UI reads.
        if path in ("/api/workflows", "/workflows"):
            self._send(200, {"workflows": workflows.listing()})
            return
        # The flowchart before the first answer of a session, and after a
        # reload. Every finished run carries the same view as `thesisPath`.
        route, _, query = path.partition("?")
        if route in ("/api/thesis_path", "/thesis_path"):
            self._send(200, {"thesisPath": _thesis_path_for(query)})
            return
        for prefix in ("/api/figure/", "/figure/"):
            if path.startswith(prefix):
                self._send_figure(path[len(prefix):])
                return
        for prefix in ("/api/table/", "/table/"):
            if path.startswith(prefix):
                self._send_table(path[len(prefix):])
                return
        self._send(404, {"error": f"no route {self.path!r}"})

    def _send_table(self, name: str) -> None:
        """The data behind `<table - t-...>` as JSON, or `<id>.png` for its
        white-backdrop image. Only an id the table index holds resolves, and
        only to a file under a folder tables may live in (core/tables.py)."""
        from core import tables

        png = name.endswith(".png")
        try:
            path = tables.resolve(name[:-4] if png else name, png=png)
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
            return
        if not png:
            self._send(200, json.loads(path.read_text(encoding="utf-8")))
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_figure(self, name: str) -> None:
        """The PNG behind `<image - name>`, or a 404 that says why.

        Resolution and the path check live in core/figure_refs.py; this only
        ships bytes. Cached briefly: a figure changes only when the figure
        step is re-run, and a reload should not refetch every image.
        """
        try:
            # A `run-` name renders the run's GeoTIFF to JPEG on first request
            # (a couple of seconds); an experiment name is a ready PNG.
            path, _ = figure_refs.resolve(name)
        except LookupError as exc:
            self._send(404, {"error": str(exc)})
            return
        except Exception as exc:                            # noqa: BLE001
            self._send(500, {"error": f"rendering {name!r} failed: "
                                      f"{type(exc).__name__}: {exc}"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg" if path.suffix == ".jpg"
                         else "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        routes = {"/api/execute_node": start_task, "/execute_node": start_task,
                  "/api/execute_node_sync": execute_node,
                  "/execute_node_sync": execute_node,
                  "/api/session/open": open_session, "/session/open": open_session}
        handler = routes.get(path)
        if handler is None:
            self._send(404, {"error": f"no route {self.path!r}"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, {"error": "request too large"})
            return

        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._send(400, {"error": f"body is not JSON: {exc}"})
            return

        try:
            self._send(200, handler(request))
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:                            # noqa: BLE001
            # A graph failure is an answer the console can show, not a dead
            # connection it has to guess about.
            self._send(200, {
                "sessionId": request.get("sessionId", ""),
                "node": "argos",
                "status": "error",
                "reply": f"The run failed: {type(exc).__name__}: {exc}",
                "events": [],
            })

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[console-api] {fmt % args}")


def main() -> None:                                         # pragma: no cover
    # Every agent prompt and flow card is loaded from the vault before the
    # port opens. A missing or refused note raises here, naming the note,
    # instead of surfacing as an agent answering from half a prompt.
    from core.spec import check_all
    loaded = check_all()
    print(f"[console-api] {len(loaded)} prompts loaded from the vault")
    print(f"[console-api] pinned session {pinned.SESSION_ID}: "
          f"{len(transcript.turns(pinned.SESSION_ID))} turn(s) in its transcript")
    print(f"[console-api] POST http://{HOST}:{PORT}/api/execute_node")
    print(f"[console-api] GET  http://{HOST}:{PORT}/api/task/<taskId>")
    print(f"[console-api] GET  http://{HOST}:{PORT}/api/figure/<region-year-figure>")
    print("[console-api] point the console at it with "
          f"VITE_API_BASE=http://{HOST}:{PORT}/api")
    # Threading: a run occupies its thread for a minute, and the poll
    # asking how it is going must not queue behind it.
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":                                  # pragma: no cover
    main()
