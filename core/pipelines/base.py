"""The pipeline runner: fixed, ordered steps that produce one named report (CP8).

A pipeline is a list of STEPS, run in order, chosen by mode. Five
kinds:

    tool     a registered tool, through the tool contract (monty_graph._run_one)
    render   one of Modules 1-10, from what is stored (core/outputs.py)
    agent    a bounded agent call whose text is stored as a deliverable
    report   Module 11 or 12, its {{llm:...}} slots written by an agent
    facts    the Flood analyst's facts sidecar (it has no template)

Rules the runner keeps:

* **A stored result is reused, never re-fetched.** Under cache `on` the store
  is the experiment; under `off` it is `runs/<run_id>/`, which holds only what
  THIS run fetched (core/cache_policy.py). So "reuse what is stored" is one
  rule for both, and an uncached run re-measures everything once.
* **A failure is the result.** A tool that fails is recorded (the experiment's
  `_failures/`, the step list, the pipeline uncertainty) and the pipeline goes
  on: the renders downstream say "Not known yet". No parameter is changed and
  nothing is retried with a relaxed threshold.
* **Every step is bounded** (non-negotiable 6). A time ceiling per step, and a
  turn ceiling for agent steps on top of the agent's own `max_turns`. A step
  past its time ceiling STOPS the pipeline: the call may still be running, so
  nothing after it can trust the store.
* **A report needs the Flood analyst run first** (D-24): checked before any
  step runs, so a refused run fetches nothing.
* **Agents write prose, never numbers.** An agent step is given the stored
  results; a report's slots are written from its facts. With no writer
  (`--no-agents`) every agent step is `pending` and its text stays a visible
  marker.

Every step emits a `pipeline-step` event and adds to the run's PIPELINE
uncertainty (4.6: how the run went). The run's record goes to
`<store>/pipeline_runs/<name>.json` and `runs/<run_id>/pipelines/<name>.json`,
its RED metrics through `observability.record_pipeline`.
"""

from __future__ import annotations

import contextvars
import json
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .. import cache_policy, observability, report_config, uncertainty
from ..experiment import Experiment
from ..logbook import event

STATUSES = ("ok", "cached", "failed", "skipped", "pending", "timed_out", "not_run")


class MissingInput(ValueError):
    """A step's input is not stored, so the step cannot run. Not a failure of
    the step: the reason is recorded and the step is skipped."""


class PipelineRefused(ValueError):
    """The pipeline cannot start: D-24, or no AOI configured."""


class StepTimeout(RuntimeError):
    pass


@dataclass
class AgentCall:
    """What an agent step asks for. A writer turns it into text."""

    agent: str
    flow: str
    task: str
    context: str
    store: Any
    max_turns: int
    # A call that needs more room than the agent's default (the recommended
    # actions of Modules 9 and 10): its own reasoning effort and token
    # ceiling. Whether reasoning is SENT stays the .env's MODEL_n_REASONING.
    effort: str = ""
    max_tokens: int = 0


Writer = Callable[[AgentCall], str]


@dataclass
class Step:
    kind: str
    name: str
    action: Callable[["Run"], dict]
    max_s: float = 900.0
    when: Callable[["Run"], tuple[bool, str]] | None = None


@dataclass
class Run:
    """One pipeline run: the store it reads and writes, and what it did."""

    pipeline: str
    experiment: str
    store: Any
    bbox: list[float]
    start: str
    end: str
    settings: dict[str, Any]
    writer: Writer | None
    cached_only: bool
    executor: Callable[[dict], dict]
    refresh: tuple[str, ...] = ()
    steps: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)

    def payload(self, name: str) -> dict | None:
        rec = self.store.cached_input(name)
        return rec.get("payload") if rec else None

    def has(self, name: str) -> bool:
        return self.store.cached_input(name) is not None

    def out(self, sub: str = "rasters") -> dict[str, str]:
        return {"output_dir": str(Path(self.store.dir) / sub), "event_id": self.store.slug}

    def params_for(self, tool: str) -> dict[str, Any]:
        """Per-experiment tool parameters (facts about the place, never thresholds).

        The weather's local clock, when the experiment configures none, is the
        event's own zone ON THE WINDOW'S FIRST DAY, as data_availability
        resolved it (BUG-017) -- never 0, which called BST "UTC+0" over
        Edinburgh. Storage stays UTC; the offset only labels local times.
        """
        out = dict((self.settings.get("tool_params") or {}).get(tool) or {})
        if tool == "fetch_weather" and "local_utc_offset_h" not in out:
            from .. import dating

            hours = dating.offset_on(dating.store_zone(self.store), date.fromisoformat(self.start))
            if hours is not None:
                out["local_utc_offset_h"] = hours
        return out

    def note(self, key: str, statement: str, value: Any = None) -> None:
        self.items.append(uncertainty.item("pipeline", key, statement, value))

    @property
    def day_before(self) -> str:
        return (date.fromisoformat(self.start) - timedelta(days=1)).isoformat()


# -- the bounded call ------------------------------------------------------------
def _bounded(fn: Callable[[], dict], max_s: float) -> dict:
    """Run `fn` with a time ceiling, in the caller's context (the cache policy,
    the run id and the event sinks are ContextVars)."""
    ctx = contextvars.copy_context()
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = ctx.run(fn)
        except BaseException as exc:                          # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(max_s)
    if t.is_alive():
        raise StepTimeout(f"no result after {max_s:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _stale(params: dict, run: Run) -> str:
    """How a stored result's request differs from this run's AOI and window,
    or "" when it does not (or the record does not say)."""
    diffs = []
    held = [round(float(v), 6) for v in params.get("bbox") or []]
    if held and held != [round(float(v), 6) for v in run.bbox]:
        diffs.append(f"bbox {params['bbox']}")
    # `start`/`end` are get_flood_map's names for the same window.
    for key, want in (("start_date", run.start), ("end_date", run.end),
                      ("start", run.start), ("end", run.end)):
        if params.get(key) and str(params[key]) != want:
            diffs.append(f"{key} {params[key]}")
    return ", ".join(diffs)


# -- step builders -------------------------------------------------------------------
# Tools whose result is stored once per product, not once per tool.
PER_PRODUCT = ("get_flood_map",)


def tool(name: str, params: Callable[[Run], dict] | dict, *, max_s: float = 900.0,
         when: Callable[[Run], tuple[bool, str]] | None = None,
         internal: bool = False) -> Step:
    """A registered tool. Its result is stored under the tool's cache name.

    `internal=True` runs a tool switched off in `workflows.INACTIVE` for one
    pipeline step only: it stays out of the catalogue, the routing and the
    chat (user decision 2026-11-22: the road check, for Module 9 only)."""
    from .. import workflows

    from ..outputs import ALIASES

    stored0 = workflows.cache_name(name)
    # A result cached by hand under an older name counts as stored too.
    names0 = next((t for t in ALIASES.values() if stored0 in t), (stored0,))

    def action(run: Run) -> dict:
        stored, names = stored0, names0     # this call's own, never shared
        if name in PER_PRODUCT:
            # Stored per product (`flood_map_<id>`): the product is known only
            # from the params, so they are built first, and a stored map of
            # ANOTHER product is never taken for this one.
            try:
                p0 = params(run) if callable(params) else dict(params)
            except MissingInput as exc:
                run.note(f"{name}_input_missing", f"{name} not run: {exc}")
                return {"status": "skipped", "detail": str(exc)}
            stored = workflows.cache_name(name, p0)
            # An older store keeps it as `get_flood_map`: reused only when it
            # is this product, so a map is never fetched twice.
            names = (stored,) + tuple(n for n in names0 if n != stored)
        found = next(((n, r) for n in names if (r := run.store.cached_input(n))), None)
        if found and name in PER_PRODUCT and found[0] != stored:
            key = stored.removeprefix("flood_map_")
            if ((found[1].get("payload") or {}).get("product")) != key:
                found = None
        if found and name not in run.refresh:
            held, rec = found
            stale = _stale(rec.get("params") or {}, run)
            if not stale:
                return {"status": "cached", "detail": f"stored `{held}` reused"}
            # Never reused, never overwritten unasked: the store is the record.
            run.note(f"{name}_stale", f"the stored {held} is for {stale}, not this run's "
                     "AOI and window; it was not reused")
            return {"status": "skipped", "detail": f"stored `{held}` is for {stale}; "
                                                   f"--refresh {name} re-fetches it"}
        if run.cached_only:
            run.note(f"{name}_not_fetched", f"{name} is not stored and a cached-only run "
                     "does not fetch it")
            return {"status": "skipped", "detail": "not stored; cached-only run"}
        if name in workflows.INACTIVE and not internal:
            run.note(f"{name}_not_available", f"{name} not run: {workflows.INACTIVE[name]}")
            return {"status": "skipped", "detail": f"not available: {workflows.INACTIVE[name]}"}
        try:
            p = params(run) if callable(params) else dict(params)
        except MissingInput as exc:
            run.note(f"{name}_input_missing", f"{name} not run: {exc}")
            return {"status": "skipped", "detail": str(exc)}
        entry = workflows.REGISTRY[name] if internal else workflows.resolve(name)[0]
        result = run.executor({"function": name, "params": p, "workflow": entry, "error": None})
        if result["status"] == "success":
            run.store.cache_input(stored, result["output"], params=p)
            return {"status": "ok", "detail": f"stored `{stored}`"}
        err = result.get("error") or {}
        run.store.record_failure(stored, err, params=p)
        run.note(f"{name}_failed", f"{name} failed: {err.get('message', '')[:200]}")
        return {"status": "failed", "detail": f"{err.get('error_type', '')}: "
                                              f"{err.get('message', '')}"[:300]}

    return Step("tool", name, action, max_s, when)


def render(slot: str) -> Step:
    def action(run: Run) -> dict:
        from ..report_render import render as render_module

        path = render_module(run.store, slot)
        return {"status": "ok", "detail": path.name}

    return Step("render", slot, action, 120.0)


def agent(slug: str, flow: str, task: Callable[[Run], str], *, deliverable: str = "",
          max_turns: int = 8, max_s: float = 900.0,
          when: Callable[[Run], tuple[bool, str]] | None = None) -> Step:
    """A flow card run by its agent; the text is stored as a deliverable."""
    name = deliverable or flow

    def action(run: Run) -> dict:
        if run.store.deliverable(name):
            return {"status": "cached", "detail": f"stored deliverable `{name}` reused"}
        if run.writer is None:
            run.note(f"{name}_pending", f"the {name.replace('_', ' ')} text is not written: "
                     "agents are off for this run")
            return {"status": "pending", "detail": "agents off"}
        call = AgentCall(slug, flow, task(run), _header(run), run.store, max_turns)
        event("agent-call", src="pipeline", pipeline=run.pipeline, agent=slug, flow=flow)
        text = run.writer(call)
        if not text.strip() or "[TRUNCATED" in text:
            # A reply cut off at max_tokens is not an answer (non-negotiable 6).
            run.note(f"{name}_cut_off", f"the {slug} reply for {flow} was empty or cut off; "
                     "nothing is stored")
            return {"status": "failed", "detail": "empty or truncated reply"}
        run.store.write_deliverable(name, text, agent=slug, flow=flow, brief=call.task[:400])
        return {"status": "ok", "detail": f"deliverable `{name}`"}

    return Step("agent", f"{slug}:{flow}", action, max_s, when)


def report(slot: str, slug: str, voice: str, *, context: Callable[[Run], str] | None = None,
           max_turns: int = 4, max_s: float = 900.0) -> Step:
    """Module 11 or 12. Rendered once for its facts; the agent writes the
    `{{llm:...}}` slots FROM those facts; rendered again with the slots. The
    second render recomputes the same facts from the same store."""

    def action(run: Run) -> dict:
        from .. import report_render

        path = report_render.render(run.store, slot, pipeline_items=run.items)
        side = json.loads(path.with_suffix(".facts.json").read_text(encoding="utf-8"))
        slots = side["llm_slots"]
        if not slots:
            return {"status": "ok", "detail": path.name}
        if run.writer is None:
            run.note(f"{slot}_slots_pending", f"{len(slots)} agent-written part(s) of the "
                     f"{slot.replace('_', ' ')} are pending: agents are off for this run")
            return {"status": "pending", "detail": f"{path.name}; {len(slots)} slot(s) pending"}
        task = _slots_task(slot, voice, slots, side["facts"])
        extra = context(run) if context else ""
        call = AgentCall(slug, "", task, _header(run) + ("\n\n" + extra if extra else ""),
                         run.store, max_turns)
        event("agent-call", src="pipeline", pipeline=run.pipeline, agent=slug, flow=f"{slot}-slots")
        written = _parse_slots(run.writer(call), {s["slot"] for s in slots})
        if not written:
            run.note(f"{slot}_slots_unreadable", "the agent's reply held no readable slot "
                     "texts; the slots stay pending")
        path = report_render.render(run.store, slot, llm=written, pipeline_items=run.items)
        missing = sorted({s["slot"] for s in slots} - set(written))
        if missing:
            run.note(f"{slot}_slots_missing", f"slots not written: {', '.join(missing)}")
        return {"status": "ok" if not missing else "pending",
                "detail": f"{path.name}; {len(written)}/{len(slots)} slot(s) written"}

    return Step("report", slot, action, max_s)


def facts(label: str) -> Step:
    def action(run: Run) -> dict:
        from ..report_render import facts_sidecar

        path = facts_sidecar(run.store, Path(run.store.dir) / "pipeline_runs" / f"{label}.facts.json",
                             label=label, pipeline_items=run.items)
        return {"status": "ok", "detail": path.name}

    return Step("facts", label, action, 300.0)


# -- agent text ------------------------------------------------------------------------
def _header(run: Run) -> str:
    """The constants every agent call carries (the session header's
    job)."""
    place = _meta(run.store, "place") or run.experiment
    return (f"### Event\n- experiment: {run.experiment}\n- place: {place}\n"
            f"- bounding box (W, S, E, N): {run.bbox}\n- window: {run.start} to {run.end}\n"
            f"- cache: {cache_policy.current()}")


def _slots_task(slot: str, voice: str, slots: list[dict], facts: list[dict]) -> str:
    lines = [f"Write the agent-written parts of the {slot.replace('_', ' ')}. {voice}",
             "Use ONLY the facts below. Write no number that is not in them; a fact marked "
             "NOT KNOWN stays not known -- say so plainly, never estimate it.",
             "When a slot quotes a figure from satellite, model or survey data, say when "
             "that data was acquired, as 'dated <date>' (for example 'dated 30 Nov 2025') "
             "copied from the data_dated lines -- the date only, no time and no time zone, "
             "and never a date of your own.",
             "Reply with one JSON object and nothing else: {\"<slot>\": \"<text>\", ...}.",
             "", "Slots (word limits are hard):"]
    lines += [f"- {s['slot']}: at most {s['max_words']} words" for s in slots]
    lines += ["", "Facts:"]
    for f in facts:
        if f.get("key") == "data_dated" and f.get("value"):
            lines.append("- data_dated (when each input was acquired):")
            lines += [f"  - {t}" for t in f["value"]]
            continue
        if f.get("status") == "not_known":
            lines.append(f"- {f['key']}: NOT KNOWN ({f.get('reason', '')})")
        else:
            shown = f.get("text") or f"{f.get('value')} {f.get('unit') or ''}".strip()
            lines.append(f"- {f['key']}: {str(shown)[:400]}")
    return "\n".join(lines)


def _parse_slots(reply: str, wanted: set[str]) -> dict[str, str]:
    match = re.search(r"\{.*\}", reply or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: str(v).strip() for k, v in data.items()
            if k in wanted and isinstance(v, (str, int, float)) and str(v).strip()}


def model_writer(call: AgentCall) -> str:
    """The real writer: the agent, its flow card and the flow's declared
    inputs from the store -- the same assembly as a specialist node. Spends
    money; the tests pass their own writer."""
    from importlib import import_module

    from ..graph import _flow_inputs, _flow_overlay

    a = import_module(f"agents.{call.agent}.agent").build()
    a.spec = replace(a.spec, max_turns=min(a.spec.max_turns, call.max_turns))
    if call.max_tokens and hasattr(a.llm, "max_tokens"):
        a.llm.max_tokens = max(a.llm.max_tokens or 0, call.max_tokens)
    if call.effort and hasattr(a.llm, "effort"):
        a.llm.effort = call.effort
    overlay, _ = _flow_overlay(call.agent, call.flow)
    fed, _ = _flow_inputs({"experiment": ""}, call.flow, call.agent, store=call.store)
    return a.run(call.task, context=call.context + fed, extra_system=overlay)


# -- the pipeline ------------------------------------------------------------------------
def _meta(store: Any, key: str) -> str:
    path = getattr(store, "memory_path", None)
    if path is None or not Path(path).exists():
        return ""
    for line in Path(path).read_text(encoding="utf-8").splitlines()[:12]:
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _window(text: str) -> tuple[str, str]:
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", text or "")
    if not m:
        raise PipelineRefused(f"the event window {text!r} is not 'YYYY-MM-DD to YYYY-MM-DD'")
    return m.group(1), m.group(2)


@dataclass
class Pipeline:
    name: str
    title: str
    steps: list[Step]
    requires_flood_analyst: bool = False

    def run(self, experiment: str, *, cache: str = "on", run_id: str = "",
            writer: Writer | None = model_writer, cached_only: bool = False,
            refresh: tuple[str, ...] | list[str] = (),
            root: Path | None = None, executor: Callable[[dict], dict] | None = None) -> dict:
        """Run the pipeline for one experiment. Returns the run's record."""
        from ..monty_graph import _run_one

        with cache_policy.use(cache, run_id) as rid:
            record = Experiment(experiment, root=root)
            if not record.memory_path.exists():
                raise PipelineRefused(f"experiment {experiment!r} does not exist "
                                      f"({record.memory_path})")
            place, window = _meta(record, "place"), _meta(record, "window")
            start, end = _window(window)
            settings = report_config.settings(record.slug)
            bbox = settings.get("bbox")
            if not bbox:
                raise PipelineRefused(f"no AOI configured for {experiment!r} in "
                                      "core/report_config.py (bbox)")
            if cache_policy.is_off():
                store = cache_policy.RunStore(rid, event=record.slug)
                store.create(place=place, window=window)
            else:
                store = record
            if self.requires_flood_analyst:
                from ..report_render import REQUIRED

                written = {s["slot"] for s in store.status() if s["written"]}
                missing = [s for s in REQUIRED if s not in written]
                if missing:
                    raise PipelineRefused(
                        f"the {self.title} needs the Flood analyst run of this AOI, window and "
                        f"cache policy first (D-24); in {store.dir} these are not written: "
                        f"{', '.join(missing)}")
            run = Run(self.name, record.slug, store, list(bbox), start, end, settings, writer,
                      cached_only, executor or _run_one, refresh=tuple(refresh))
            run.note("cache", f"cache {cache_policy.current()}: "
                     + ("stored results are reused" if not cache_policy.is_off()
                        else "every tool re-fetched into this run's folder"))
            return self._loop(run, rid)

    def _loop(self, run: Run, rid: str) -> dict:
        status = "ok"
        with observability.Timer() as total:
            for n, step in enumerate(self.steps, start=1):
                if status == "stopped":
                    run.steps.append({"n": n, "kind": step.kind, "name": step.name,
                                      "status": "not_run", "detail": "an earlier step timed out",
                                      "duration_s": 0.0})
                    continue
                with observability.Timer() as t:
                    out = self._step(run, step)
                row = {"n": n, "kind": step.kind, "name": step.name, "status": out["status"],
                       "detail": out.get("detail", ""), "duration_s": round(t.seconds, 3)}
                run.steps.append(row)
                event("pipeline-step", src="pipeline", pipeline=self.name, n=n, step_kind=step.kind,
                      step=step.name, status=out["status"], duration_s=row["duration_s"])
                if out["status"] == "timed_out":
                    status = "stopped"
                elif out["status"] in ("failed", "skipped", "pending") and status == "ok":
                    status = "degraded"
        record = {"pipeline": self.name, "title": self.title, "experiment": run.experiment,
                  "run_id": rid, "cache": cache_policy.current(), "status": status,
                  "store": str(run.store.dir), "window": [run.start, run.end], "bbox": run.bbox,
                  "duration_s": round(total.seconds, 3), "steps": run.steps,
                  "uncertainty": {"pipeline": run.items}}
        self._write(run, rid, record)
        observability.record_pipeline(self.name, status, total.seconds,
                                      steps=len(run.steps),
                                      failed=sum(1 for s in run.steps if s["status"] == "failed"))
        return record

    def _step(self, run: Run, step: Step) -> dict:
        if step.when is not None:
            ok, why = step.when(run)
            if not ok:
                return {"status": "skipped", "detail": why}
        try:
            return _bounded(lambda: step.action(run), step.max_s)
        except StepTimeout as exc:
            run.note(f"{step.name}_timed_out", f"{step.name} passed its {step.max_s:.0f} s "
                     "ceiling; the pipeline stopped there")
            return {"status": "timed_out", "detail": str(exc)}
        except MissingInput as exc:
            run.note(f"{step.name}_input_missing", f"{step.name} not run: {exc}")
            return {"status": "skipped", "detail": str(exc)}
        except Exception as exc:                               # noqa: BLE001
            run.note(f"{step.name}_failed", f"{step.name} failed: {type(exc).__name__}: {exc}"[:240])
            return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"[:300]}

    def _write(self, run: Run, rid: str, record: dict) -> None:
        text = json.dumps(record, indent=1, default=str)
        targets = [Path(run.store.dir) / "pipeline_runs" / f"{self.name}.json"]
        if (folder := cache_policy.run_dir(rid)) is not None:
            targets.append(folder / "pipelines" / f"{self.name}.json")
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
