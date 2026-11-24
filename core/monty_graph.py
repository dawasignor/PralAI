"""Monty's internal graph -- a sub-graph invoked by the `monty` node.

    [Argos: event_id + task list]
        -> receive_input      validates the payload
        -> resolve_tasks      looks every function up in the registry
        -> execute_tasks      runs them in order, isolating failures
        -> return_result      assembles result_1..result_N
    [Argos: reads results]

Monty owns the code it runs. Tasks are first-party functions registered in
`core/workflows/`, called by name -- effectively a batch of tool calls in one
request. There is no external provider and nothing is fetched.

State channels
--------------
`event_id`, `tasks`, `resolved`, `results` and `error` are **overwrite**
channels -- each node replaces the value it owns. `execution_logs`
(plain-English steps) and `exchange_log` (the formatted transcript) are
**append** channels.

Failure isolation: one task raising does not stop the batch. Every task gets a
`result_N` entry either way, and the batch is reported as an error only if at
least one task failed -- which is what tells Argos to put its debugger on.
"""

from __future__ import annotations

import inspect
import json
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import cache_policy, dating, observability, redact, uncertainty, workflows
from .logbook import event, log


class MontyState(TypedDict, total=False):
    event_id: Any                             # carried through, never interpreted
    tasks: list[dict[str, Any]]               # [{"function": str, "params": {...}}]
    resolved: list[dict[str, Any]]            # per-task: callable or a resolve error
    results: dict[str, Any]                   # result_1..result_N
    error: dict[str, Any] | None              # first failure, for Argos' debugger
    execution_logs: Annotated[list[str], operator.add]
    exchange_log: Annotated[list[str], operator.add]
    timestamp: str
    attempt: int                              # 1 on first try, 2+ on a retry
    debug: dict[str, Any]                     # Argos' patch, empty on attempt 1


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%b %d %H:%M:%S")


def _brief(value: Any, limit: int = 90) -> str:
    """Compact a value for the log -- series are long and unreadable raw."""
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else f"{text[:limit]}... ({len(text)} chars)"


# ---------------------------------------------------------------------------
# Step 1 -- receive input from Argos
# ---------------------------------------------------------------------------
def receive_input(state: MontyState) -> dict:
    attempt = state.get("attempt", 1)
    debug = state.get("debug") or {}
    tasks = list(state.get("tasks") or [])

    # A retry may carry a corrected task list. It REPLACES the original: a
    # merge can add or change an entry but never remove one, and "you sent a
    # parameter the function does not accept" is the most common failure.
    patched = debug.get("patched_tasks")
    note = ""
    if patched:
        tasks = list(patched)
        note = f" (tasks replaced by Argos: {len(tasks)})"

    malformed = [
        f"tasks[{i}] must be an object with a 'function' name"
        for i, task in enumerate(tasks)
        if not isinstance(task, dict) or not task.get("function")
    ]
    error = None
    if not tasks:
        error = {"stage": "receive_input", "error_type": "bad_payload",
                 "message": "no tasks supplied", "task": None,
                 "hint": "send tasks: [{'function': ..., 'params': {...}}]"}
    elif malformed:
        error = {"stage": "receive_input", "error_type": "bad_payload",
                 "message": "; ".join(malformed), "task": None,
                 "hint": "every task needs a 'function' key"}

    line = log("monty", None, "receive_input",
               f"attempt={attempt} event_id={state.get('event_id')} "
               f"tasks={[t.get('function') for t in tasks if isinstance(t, dict)]}{note}")
    return {
        "tasks": tasks,
        "timestamp": _now(),
        "error": error,
        "results": {},
        "execution_logs": [f"Received {len(tasks)} task(s) from Argos.{note}"],
        "exchange_log": [line],
    }


# ---------------------------------------------------------------------------
# Step 2 -- resolve every function name against the registry
# ---------------------------------------------------------------------------
def resolve_tasks(state: MontyState) -> dict:
    resolved: list[dict[str, Any]] = []
    renames: list[str] = []
    for task in state.get("tasks") or []:
        name = task.get("function")
        entry, note = workflows.resolve(name)
        error = None
        if note == "renamed":
            # Followed, not refused: an old name is a habit, not a mistake,
            # and the result carries the current name so it is cached and
            # counted as one tool.
            renames.append(f"{name} -> {entry.name}")
            name = entry.name
        elif note == "inactive":
            error = {"error_type": "unknown_function",
                     "message": f"{name!r} is not available "
                                f"({workflows.INACTIVE[workflows.canonical(name)]})",
                     "hint": f"registered: {workflows.names()}"}
        elif note == "retired":
            replacement, reason = workflows.RETIRED[name]
            error = {"error_type": "unknown_function",
                     "message": f"{name!r} was retired; use {replacement!r} "
                                f"({reason})",
                     "hint": f"registered: {workflows.names()}"}
        elif entry is None:
            error = {"error_type": "unknown_function",
                     "message": f"no workflow named {name!r}",
                     "hint": f"registered: {workflows.names()}"}
        resolved.append({
            "function": name,
            "params": task.get("params") or {},
            "workflow": entry,
            "error": error,
        })
    rename_lines = [log("monty", None, "renamed-tool", r) for r in renames]

    known = [r["function"] for r in resolved if r["workflow"]]
    missing = [r["function"] for r in resolved if not r["workflow"]]
    line = log("monty", None, "resolve_tasks",
               f"resolved={known}" + (f" unknown={missing}" if missing else ""))
    return {
        "resolved": resolved,
        "execution_logs": [f"Resolved {len(known)}/{len(resolved)} task(s)."],
        "exchange_log": [*rename_lines, line],
    }


# ---------------------------------------------------------------------------
# Step 3 -- run them, isolating failures
# ---------------------------------------------------------------------------
def _failed(name: str | None, error: dict[str, Any]) -> dict[str, Any]:
    """A failed task result, shaped exactly like a successful one.

    `message` is present on both paths, so a consumer can read
    `result["message"]` without checking status first. It is mirrored from
    `error["message"]` rather than written out a second time, so the two cannot
    drift apart.
    """
    return {"task": name, "output": None, "status": "error",
            "message": error.get("message", f"{name} failed"), "error": error}


def _run_one(entry: dict[str, Any], attempt: int = 1) -> dict[str, Any]:
    """Execute one task under the tool contract. Never raises.

    The contract wraps the call, not the tool: timing,
    the RED metrics, the tool-start / tool-end events and the `run` block are
    added here, so every registered tool gets them whether or not it has been
    converted yet. The failure shape is untouched -- still the five keys of
    `_failed` -- because Argos' debugger and the console read exactly those.
    """
    name = entry["function"]
    event("tool-start", src="monty", tool=name or "?", attempt=attempt,
          cache=cache_policy.current())
    started = datetime.now(timezone.utc)
    with observability.tool_scope() as source_failures,             observability.Timer() as timer:
        result = _call(entry)
    status = result["status"]
    error_type = (result.get("error") or {}).get("error_type", "")

    # A dropped call that its retry answered is a retry, not a failure: the
    # tool got the data (compare.py sets a fact aside only for a failure).
    source_failures, source_retries = observability.split_failures(list(source_failures))
    if status == "success":
        run = {"run_id": cache_policy.run_id(), "cache": cache_policy.current(),
               "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "duration_s": round(timer.seconds, 3), "attempts": attempt,
               "source_failures": source_failures, "source_retries": source_retries}
        output, notes = workflows.apply_contract(result["output"], run)
        if notes:
            log("monty", None, "contract-missing", f"{name}: {', '.join(notes)}")
        # The steps of the call and the uncertainty in each (core/uncertainty.py):
        # read off the payload, stored with it, and logged for Entro.
        if isinstance(output, dict):
            steps, block = uncertainty.capture(name, output, entry["params"])
            # The tool's standing statements (data source, method) and the
            # model-uncertainty todo travel with every stored result.
            block["standing"] = workflows.tool_uncertainty(name)
            output["uncertainty"] = block
            output["run"]["steps"] = steps
            uncertainty.record(name, steps, block, output["run"])
            # Every GeoTIFF the call wrote carries its source and acquisition
            # date in the file (BUG-020), so it says both away from the report.
            if dating.raster_paths(output):
                stored = workflows.cache_name(name)
                zone = dating.zone_at(dating.bbox_of(entry["params"], output))
                output["run"]["rasters_tagged"] = dating.tag_rasters(
                    stored, output, zone, since=started.timestamp())
        result["output"] = output

    observability.record_tool(name or "?", status, timer.seconds,
                              attempt=attempt, error_type=error_type,
                              source_failures=len(source_failures))
    event("tool-end", src="monty", tool=name or "?", status=status,
          duration_s=round(timer.seconds, 3), attempt=attempt,
          **({"error_type": error_type} if error_type else {}))
    return result


# Parameters every tool treats as a folder or file name; coerced to str.
PATH_PARAMS = ("event_id", "output_dir", "figure_dir", "config", "flood_raster",
               "extent_raster", "permanent_water_raster", "osm_context")


def _call(entry: dict[str, Any]) -> dict[str, Any]:
    """Bind and run one task. Returns the per-task result; never raises."""
    name, params = entry["function"], dict(entry["params"])

    if entry["error"]:                                  # unresolved name
        return _failed(name, {"stage": "resolve_tasks", "task": name,
                              **entry["error"]})

    fn = entry["workflow"].fn
    # An uncached run re-fetches everything, including a tool's own disk
    # cache. The policy wins over a use_cache Argos may have sent: an RQ5 run
    # that quietly read GLO-30 from disk would not measure what it claims.
    if cache_policy.is_off() and "use_cache" in inspect.signature(fn).parameters:
        if params.get("use_cache") is not False:
            event("cache-state", src="monty", tool=name,
                  note="off: use_cache=False passed")
        params["use_cache"] = False
    # Path-like parameters are strings. Argus' batch carries a numeric
    # `event_id` (42) and copies it into params; every tool joins it onto a
    # folder (`Path(output_dir) / event_id`), and Path / int raised TypeError
    # in data_availability, where_am_i and the rest (user bug, 2026-11-02).
    for key in PATH_PARAMS:
        if key in params and params[key] is not None and not isinstance(params[key], str):
            params[key] = str(params[key])
        # The model sees repo paths as `<repo>/...` (core/redact.prompt_text).
        if isinstance(params.get(key), str):
            params[key] = redact.expand(params[key])
    # An explicit null folder means "not given": dropped, so the tool's own
    # default applies. Passed through, tools that build paths with an
    # f-string wrote under a folder literally named "None" (user bug,
    # 2026-11-17: the JRC map). Only the folder: a null `event_id` or raster
    # path keeps its meaning for the tool that reads it.
    for key in ("output_dir", "figure_dir"):
        if key in params and params[key] in (None, ""):
            del params[key]
    try:
        inspect.signature(fn).bind(**params)
    except TypeError as exc:
        return _failed(name, {
            "stage": "execute_tasks", "task": name,
            "error_type": "signature_mismatch", "message": str(exc),
            "signature": str(inspect.signature(fn)),
            "supplied": sorted(params),
        })

    try:
        output = fn(**params)
    except Exception as exc:                            # any task failure
        import traceback
        return _failed(name, {
            "stage": "execute_tasks", "task": name,
            "error_type": "runtime_error",
            "message": f"{type(exc).__name__}: {exc}",
            # The exception's class, so a consumer can act on WHAT failed
            # without reading the message (the land-cover fallback keys on
            # BelowValidFraction). The parameters as actually called, after
            # the cache policy applied.
            "exception": type(exc).__name__,
            "params": params,
            "traceback": traceback.format_exc()[-800:],
        })

    try:
        json.dumps(output)
    except (TypeError, ValueError) as exc:
        return _failed(name, {
            "stage": "execute_tasks", "task": name,
            "error_type": "unserialisable",
            "message": f"{name} returned {type(output).__name__}: {exc}",
        })

    return {"task": name, "output": output, "status": "success",
            "message": f"{name} completed successfully", "error": None}


def execute_tasks(state: MontyState) -> dict:
    results: dict[str, Any] = {}
    logs: list[str] = []
    lines: list[str] = []
    first_error: dict[str, Any] | None = None

    for i, entry in enumerate(state.get("resolved") or [], start=1):
        started = log("monty", None, "run", f"result_{i} {entry['function']}(...)")
        result = _run_one(entry, attempt=state.get("attempt", 1))
        results[f"result_{i}"] = result

        if result["status"] == "success":
            done = log("monty", None, "ok",
                       f"result_{i} {_brief(result['output'])}")
            logs.append(f"result_{i}: {result['message']}.")
        else:
            err = result["error"]
            done = log("monty", None, "FAILED",
                       f"result_{i} {err['error_type']}: {err['message']}")
            logs.append(f"result_{i}: {entry['function']} failed "
                        f"({err['error_type']}).")
            first_error = first_error or err

        lines += [started, done]

    return {"results": results, "error": first_error,
            "execution_logs": logs, "exchange_log": lines}


# ---------------------------------------------------------------------------
# Step 4 -- return results to Argos
# ---------------------------------------------------------------------------
def return_result(state: MontyState) -> dict:
    attempt = state.get("attempt", 1)
    results = state.get("results") or {}
    error = state.get("error")
    failed = [key for key, r in results.items() if r.get("status") != "success"]

    if error and not results:
        # Bad payload -- the batch never got as far as running anything.
        line = log("monty", "argos", "ERROR",
                   f"attempt={attempt} {error['error_type']}: {error['message']}")
    elif failed:
        line = log("monty", "argos", "PARTIAL",
                   f"attempt={attempt} {len(results) - len(failed)}/{len(results)} "
                   f"ok, failed={failed}")
    else:
        line = log("monty", "argos", "OK",
                   f"attempt={attempt} {len(results)}/{len(results)} tasks ok")

    # `error` is already on the channel from whichever node set it; the caller
    # derives batch status from it plus the per-task statuses in `results`.
    return {
        "results": results,
        "execution_logs": [f"Returned {len(results)} result(s) to Argos."],
        "exchange_log": [line],
    }


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def _after_receive(state: MontyState) -> str:
    return "return" if state.get("error") else "resolve"


def build_monty_graph():
    builder = StateGraph(MontyState)
    builder.add_node("receive_input", receive_input)
    builder.add_node("resolve_tasks", resolve_tasks)
    builder.add_node("execute_tasks", execute_tasks)
    builder.add_node("return_result", return_result)

    builder.add_edge(START, "receive_input")
    builder.add_conditional_edges(
        "receive_input", _after_receive,
        {"resolve": "resolve_tasks", "return": "return_result"},
    )
    builder.add_edge("resolve_tasks", "execute_tasks")
    builder.add_edge("execute_tasks", "return_result")
    builder.add_edge("return_result", END)
    return builder.compile()


def run_monty(payload: dict[str, Any], *, attempt: int = 1,
              debug: dict[str, Any] | None = None) -> MontyState:
    """Run one Monty attempt over a batch of tasks.

    `payload` is {"event_id": ..., "tasks": [{"function", "params"}, ...]}.
    """
    payload = payload or {}
    return build_monty_graph().invoke(
        MontyState(
            event_id=payload.get("event_id"),
            tasks=list(payload.get("tasks") or []),
            resolved=[],
            results={},
            error=None,
            execution_logs=[],
            exchange_log=[],
            timestamp=_now(),
            attempt=attempt,
            debug=debug or {},
        )
    )
