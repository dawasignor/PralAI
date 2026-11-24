"""Modules 9 and 10: hazard, exposure, vulnerability, risk and action
(user decision 2026-11-02).

One procedure, used by the Flood analyst pipeline and by the chat (a Chanakya
flow whose card names `module:`), so the two can never write a module two
ways:

    Monty      get_flood_map -> get_pop_data (with the flood extent)
               -> fetch_inform_risk -> search_previous_disasters
               -> map_hazard_exposure
    render     the module's facts, from its live vault template
    Norwood    hands the flood map over to Chanakya: what it shows, in
               numbers copied from the facts, no interpretation
    Chanakya   writes the hazard reading, the two vulnerability points and
               the numbered risks, from the facts, the template's hints and
               Norwood's hand-over
    Chanakya   writes the recommended actions in a call of its own, with the
               other sections as its input
    render     again, with the agent-written parts

Agents write prose, never numbers (core/pipelines/base.py): each slot is
written from the facts the first render computed, and the second render
recomputes the same facts from the same store. Every hand-off is logged as
one agent addressing the next, so the conversation shows in the session log.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..logbook import event, log
from .base import (AgentCall, MissingInput, Run, Step, _header, _parse_slots, _slots_task,
                   tool)
from .flood_analyst import flood_map_params, observed_extent

# Slot -> the flow card that runs it and the map it shows.
MODULES = {"essential_services_hev": {"flow": "essential_services_hev",
                                      "map": "essential_services"},
           "human_health_risks_hev": {"flow": "human_health_risks_hev", "map": "health"}}
HANDOFF_FLOW = "flood_map_handoff"
ACTIONS_SLOT = "actions"

# The reader, by persona. Flood analyst mode is `norwood`.
VOICES = {
    "flood_bot": "The reader is the public: short, calm sentences, no jargon.",
    "chanakya": "The reader is a mayor: decisions first, each with its reason, in plain words.",
    "norwood": "The reader is a flood analyst: precise and crisp, every figure with its source.",
}


# -- the data the modules rest on -------------------------------------------------------
def iso3(run: Run) -> str:
    """The country, as an ISO3 code: the experiment's setting, else the
    country under the AOI's centre (Nominatim, then the World Bank's record)."""
    code = run.settings.get("country_iso3")
    if code:
        return code
    from ..places import country_code_at
    from ..workflows.context import iso3_from_iso2

    w, s, e, n = run.bbox
    iso2 = country_code_at((w + e) / 2, (s + n) / 2)
    if not iso2:
        raise MissingInput("the country of the AOI could not be found")
    code = iso3_from_iso2(iso2)
    run.settings["country_iso3"] = code
    return code


def pop_params(run: Run) -> dict:
    # The extent the product READ, flood or not: with "no flood mapped" the
    # tool still counts the AOI and the people on land it did not observe.
    return {"bbox": run.bbox, "country_iso3": iso3(run), "year": int(run.start[:4]),
            "extent_raster": observed_extent(run)["raster"]["path"], **run.out()}


def map_params(run: Run) -> dict:
    ext = observed_extent(run)
    return {"bbox": run.bbox, "flood_raster": ext["raster"]["path"],
            "flood_label": ext.get("label", ""),
            "figure_dir": str(Path(run.store.dir) / "figures"), **run.out()}


def _population_step() -> Step:
    """get_pop_data, re-run when the stored count has no figure for THIS
    flood extent (a count from before the extent existed is reused for
    nothing but the AOI total, so it is replaced)."""
    step = tool("get_pop_data", pop_params)
    inner = step.action

    def action(run: Run) -> dict:
        pop = run.payload("population") or {}
        try:
            want = observed_extent(run)["raster"]["path"]
        except MissingInput:
            want = None
        # Re-run for another extent, and for a count stored before the tool
        # drew its population map (2026-11-18).
        if want and ((pop.get("flood_extent") or {}).get("extent_raster") != want
                     or not (pop.get("figure") or {}).get("path")):
            run.refresh = tuple(run.refresh) + ("get_pop_data",)
        return inner(run)

    return Step(step.kind, step.name, action, step.max_s, step.when)


def _map_step() -> Step:
    """map_hazard_exposure, re-drawn when the stored map is of another extent."""
    step = tool("map_hazard_exposure", map_params, max_s=900.0)
    inner = step.action

    def action(run: Run) -> dict:
        hm = run.payload("map_hazard_exposure") or {}
        try:
            want = observed_extent(run)["raster"]["path"]
        except MissingInput:
            want = None
        if want and (hm.get("flood") or {}).get("raster") != want:
            run.refresh = tuple(run.refresh) + ("map_hazard_exposure",)
        return inner(run)

    return Step(step.kind, step.name, action, step.max_s, step.when)


def roads_params(run: Run) -> dict:
    """The road check on the extent the product READ: with "no flood mapped"
    it still says which main roads lie on ground it did not observe. No depth:
    depth is switched off, so no road is classed passable or not."""
    return {"bbox": run.bbox, "extent_raster": observed_extent(run)["raster"]["path"]}


def _roads_step() -> Step:
    """assess_road_access for Module 9 only (user decision 2026-11-22): the
    tool stays switched off in the catalogue and the chat (`internal`). Re-run
    when the stored check is of another extent or predates the main-road list."""
    step = tool("assess_road_access", roads_params, max_s=900.0, internal=True)
    inner = step.action

    def action(run: Run) -> dict:
        r = run.payload("assess_road_access") or {}
        try:
            want = observed_extent(run)["raster"]["path"]
        except MissingInput:
            want = None
        if r and want and (r.get("extent_raster") != want or "main_roads" not in r):
            run.refresh = tuple(run.refresh) + ("assess_road_access",)
        return inner(run)

    return Step(step.kind, step.name, action, step.max_s, step.when)


def data_steps(slot: str = "essential_services_hev") -> list[Step]:
    """The data each module rests on; the road check is Module 9's alone."""
    return [
        tool("get_flood_map", flood_map_params, max_s=1800.0),
        _population_step(),
        tool("fetch_inform_risk", lambda r: {"country_iso3": iso3(r), "before": r.day_before}),
        tool("search_previous_disasters", lambda r: {"country_iso3": iso3(r),
                                                     "before": r.day_before, "bbox": r.bbox}),
        _map_step(),
        *([_roads_step()] if slot == "essential_services_hev" else []),
    ]


# -- the agents' part --------------------------------------------------------------------
def hints(slot: str) -> str:
    """The template note's "## Hints for Chanakya" section: sourced evidence
    for the agent writing the slots, never rendered into the module."""
    from .. import vault
    from ..report_render import TEMPLATES

    try:
        note = vault.read_note(vault.build_index(), TEMPLATES[slot])
    except Exception:                                          # noqa: BLE001
        return ""
    m = re.search(r"^## Hints for Chanakya\s*\n(.*?)(?=^## |\Z)", note["content"], re.S | re.M)
    return m.group(1).strip() if m else ""


def _facts_of(path: Path) -> tuple[list[dict], list[dict]]:
    side = json.loads(path.with_suffix(".facts.json").read_text(encoding="utf-8"))
    return side["facts"], side["llm_slots"]


HAZARD_FACTS = ("hazard_flood", "hazard_product_mismatch", "hev_exposure_essential_services",
                "hev_exposure_health", "hev_food_clusters", "hev_people", "hev_roads_bridges")

# User rule 2026-11-22: the module already shows every number in its map,
# tables and figures; the agent-written parts interpret, they do not repeat.
INTERPRET = (
    "## How to write (hard rules)\n"
    "- The module already shows every number in the facts, in its map, tables and figures. "
    "Do NOT repeat them. Say what they mean and what follows from them, in words "
    "(\"most hospitals lie on ground the satellite did not see\", not \"13 of 14 hospitals\"). "
    "A number from the hints (a chlorine residual, say) may be given with its source.\n"
    "- Crisp: one short line per point, no point restating another, no part restating "
    "another part. Well inside each word limit.\n"
    "- Connectivity: where a main road or a bridge is in the flood or on ground not "
    "observed (hev_roads_bridges), say what losing it means -- which places, services or "
    "routes it cuts off, and whether hospitals, water and markets can still be reached. "
    "Name the road or bridge; it is a risk and needs an action.")


def _handoff_task(slot: str, facts: list[dict]) -> str:
    shown = [f for f in facts if f.get("key") in HAZARD_FACTS]
    lines = [f"Hand the flood map over to Chanakya for the {slot.replace('_', ' ')}.",
             "Say what the map shows: the flood, how much of the land was observed, which "
             "facilities are inside it, outside it or on ground not observed, and the people. "
             "Copy every number from the facts below; add none, and do not interpret risk -- "
             "that is Chanakya's part. At most 120 words.", "", "Facts:"]
    for f in shown:
        if f.get("status") == "not_known":
            lines.append(f"- {f['key']}: NOT KNOWN ({f.get('reason', '')})")
        else:
            lines.append(f"- {f['key']}: {str(f.get('text') or f.get('value'))[:900]}")
    return "\n".join(lines)


def _actions_task(slot: str, voice: str, limit: int, written: dict[str, str],
                  facts_task: str) -> str:
    done = "\n".join(f"### {k}\n{v}" for k, v in written.items())
    return (f"Write the recommended actions of the {slot.replace('_', ' ')}. {voice}\n"
            "This is the most important part: reason from the hazard, the exposure, the "
            "vulnerability and the risks already written below, and recommend what to do, "
            "most urgent first, each action tied to the risk it answers and, where the hints "
            "give one, to its source in short form (e.g. WHO 2005). Recommend nothing the "
            "facts and hints do not support. Write no number that is not in the facts.\n"
            f"{INTERPRET}\n"
            f"Reply with one JSON object and nothing else: {{\"{ACTIONS_SLOT}\": \"<text>\"}}. "
            f"At most {limit} words; a numbered list is welcome.\n\n"
            f"## The sections already written\n{done}\n\n{facts_task}")


def words(text: str) -> int:
    from ..report_render import shape_points

    return len(shape_points(text).split())


def _shorten(run: Run, flow: str, slot: str, voice: str, slots: list[dict],
             written: dict[str, str], header: str) -> dict[str, str]:
    """One request to shorten every part over its word limit (user, 2026-11-22:
    points were being dropped behind a " ..."). The shorter reply is kept; a
    part still over is rendered whole and flagged, never cut."""
    limits = {s["slot"]: s["max_words"] for s in slots}
    over = {k: v for k, v in written.items() if k in limits and words(v) > limits[k]}
    if not over:
        return written
    parts = "\n\n".join(f"### {k} (at most {limits[k]} words; now {words(v)})\n{v}"
                        for k, v in over.items())
    keys = ", ".join(f'"{k}": "<text>"' for k in over)
    task = (f"These parts of the {slot.replace('_', ' ')} are over their word limits. {voice}\n"
            "Shorten each to its limit. Keep every risk, action and conclusion; drop "
            "repetition and any number the module already shows. Add nothing new.\n"
            f"Reply with one JSON object and nothing else: {{{keys}}}.\n\n{parts}")
    event("agent-call", src="pipeline", pipeline=run.pipeline, agent="citadel",
          flow=f"{slot}-shorten")
    again = _parse_slots(run.writer(AgentCall("citadel", flow, task, header, run.store, 3)),
                         set(over))
    out = dict(written)
    for k, v in again.items():
        if words(v) < words(out[k]):
            out[k] = v
    still = sorted(k for k in over if words(out[k]) > limits[k])
    log("citadel", None, "shortened", ", ".join(sorted(again)) or "none readable")
    if still:
        run.note(f"{slot}_over_limit", "over the word limit after one request to shorten: "
                 f"{', '.join(still)}; shown whole and flagged")
    return out


def module(slot: str, persona: str = "norwood", *, max_s: float = 1500.0) -> Step:
    """Render Module 9 or 10 with the agent conversation above."""
    spec = MODULES[slot]

    def action(run: Run) -> dict:
        from .. import report_render

        path = report_render.render(run.store, slot, pipeline_items=run.items, persona=persona)
        facts, slots = _facts_of(path)
        if not slots:
            return {"status": "ok", "detail": path.name}
        if run.writer is None:
            run.note(f"{slot}_slots_pending", f"{len(slots)} agent-written part(s) of the "
                     f"{slot.replace('_', ' ')} are pending: agents are off for this run")
            return {"status": "pending", "detail": f"{path.name}; {len(slots)} slot(s) pending"}
        voice = VOICES.get(persona, VOICES["norwood"])
        header = _header(run)

        # 1. Norwood hands the map over.
        handoff = ""
        call = AgentCall("norwood", HANDOFF_FLOW, _handoff_task(slot, facts), header,
                         run.store, 3)
        event("agent-call", src="pipeline", pipeline=run.pipeline, agent="norwood",
              flow=HANDOFF_FLOW)
        reply = run.writer(call)
        if reply.strip() and "[TRUNCATED" not in reply:
            handoff = reply.strip()
            run.store.write_deliverable(f"{slot}_handoff", handoff, agent="norwood",
                                        flow=HANDOFF_FLOW, brief=call.task[:400])
            log("norwood", "citadel", "handoff", handoff[:160])
        else:
            run.note(f"{slot}_handoff_missing", "the remote sensing expert's hand-over was empty "
                     "or cut off; the analyst wrote from the facts alone")
            log("norwood", "citadel", "handoff-missing", "empty or cut off")

        # 2. Chanakya: hazard reading, vulnerability, risks.
        first = [s for s in slots if s["slot"] != ACTIONS_SLOT]
        facts_task = _slots_task(slot, voice, first, facts)
        hint = hints(slot)
        task = (facts_task + "\n\n" + INTERPRET
                + (f"\n\n## Hints for Chanakya (sourced evidence from the template)\n{hint}"
                   if hint else "")
                + (f"\n\n## The remote sensing expert's hand-over of the flood map\n{handoff}"
                   if handoff else ""))
        # 6 turns, Chanakya's own ceiling (agents.md): the flow card reads its
        # knowledge and sources notes first, and at 4 turns that reading used
        # every turn in 2 of 8 module calls of rq5-batch-1 (1 of 4 slots
        # written). The last turn stays reserved for the answer.
        call = AgentCall("citadel", spec["flow"], task, header, run.store, 6)
        event("agent-call", src="pipeline", pipeline=run.pipeline, agent="citadel",
              flow=f"{slot}-slots")
        written = _parse_slots(run.writer(call), {s["slot"] for s in first})
        log("citadel", None, "slots", ", ".join(sorted(written)) or "none readable")

        # 3. Chanakya: the recommended actions, in a call of its own.
        act = next((s for s in slots if s["slot"] == ACTIONS_SLOT), None)
        if act is not None:
            call = AgentCall("citadel", spec["flow"],
                             _actions_task(slot, voice, act["max_words"], written,
                                           facts_task + (f"\n\n## Hints for Chanakya\n{hint}"
                                                         if hint else "")),
                             header, run.store, 4, effort="high", max_tokens=24000)
            event("agent-call", src="pipeline", pipeline=run.pipeline, agent="citadel",
                  flow=f"{slot}-actions")
            written.update(_parse_slots(run.writer(call), {ACTIONS_SLOT}))
            log("citadel", "argos", "actions",
                "written" if ACTIONS_SLOT in written else "not readable")

        # 4. Anything over its limit goes back once to be shortened, never cut.
        written = _shorten(run, spec["flow"], slot, voice, slots, written, header)

        path = report_render.render(run.store, slot, llm=written, pipeline_items=run.items,
                                    persona=persona)
        missing = sorted({s["slot"] for s in slots} - set(written))
        if missing:
            run.note(f"{slot}_slots_missing", f"slots not written: {', '.join(missing)}")
        return {"status": "ok" if not missing else "pending",
                "detail": f"{path.name}; {len(written)}/{len(slots)} slot(s) written"}

    return Step("report", slot, action, max_s)


def steps(slot: str, persona: str = "norwood") -> list[Step]:
    return [*data_steps(slot), module(slot, persona)]


# -- the chat ------------------------------------------------------------------------------
def run_for_session(state: dict, slot: str, persona: str, *, writer=None,
                    executor=None) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Write Module 9 or 10 for a chat session: the data steps, then the
    agent conversation, on the session's own store. Returns (the module's
    text, log lines, the figures to show). The tools run as Monty's would."""
    from datetime import date

    from .. import figure_refs, report_config
    from ..graph import _store
    from ..monty_graph import _run_one
    from .base import Pipeline, model_writer

    record = state.get("session")
    if record is None or not record.place.bbox or not (record.window.start and record.window.end):
        raise MissingInput("the session has no AOI and window yet")
    store = _store(state)
    store.create()
    bbox = [float(v) for v in record.place.bbox]
    start, end = record.window.start[:10], record.window.end[:10]
    for day in (start, end):
        date.fromisoformat(day)             # a malformed window stops here, not mid-run
    settings = dict(report_config.settings(getattr(store, "slug", "")))
    run = Run("chat", getattr(store, "slug", ""), store, bbox, start, end, settings,
              writer or model_writer, False, executor or _run_one)
    lines = []
    for n, step in enumerate(steps(slot, persona or "norwood"), start=1):
        out = Pipeline._step(None, run, step)
        lines.append(log("monty" if step.kind == "tool" else "citadel", None,
                         "module-step", f"{step.name}: {out['status']} {out.get('detail', '')}"[:200]))
        run.steps.append({"n": n, "name": step.name, **out})
        if out["status"] == "timed_out":
            break
    text = _module_text(store, slot)

    text, figures = _chat_figures(text, store, record, lines)
    return text, lines, figures


IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _chat_figures(text: str, store: Any, record: Any,
                  lines: list[str]) -> tuple[str, list[dict[str, Any]]]:
    """Every figure the module placed, as a `run-` placeholder the console shows.

    The console never gets a file path. This used to register the hazard map
    only and DELETE every other image link, so the INFORM meters, the INFORM
    trend and the disasters timeline were written into the module and never
    reached the chat (user bug, 2026-11-18). Now each image the module placed
    is registered, with the caption from the store's figures index (else its
    alt text). A link to a file that is not there, or not inside a session or
    outputs folder, is dropped and logged -- never shown as a path.
    """
    from .. import figure_refs

    base = Path(getattr(store, "dir", "."))
    captions: dict[str, str] = {}
    try:
        for entry in json.loads((base / "figures" / "index.json").read_text(encoding="utf-8")):
            captions[Path(entry.get("png", "")).stem] = entry.get("caption", "")
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    charts, links = {}, {}
    for m in IMAGE.finditer(text):
        alt, raw = m.group(1), m.group(2).strip()
        path = Path(raw)
        path = (path if path.is_absolute() else base / path).resolve()
        name = re.sub(r"[^a-z0-9_]", "_", path.stem.lower()).strip("_")[:64]
        if not path.is_file() or not name:
            lines.append(log("citadel", None, "figure-missing", f"{raw}: no such file"))
            continue
        charts[name] = {"output": {"figure": {"name": name, "path": str(path),
                                               "caption": captions.get(path.stem) or alt}}}
        links[m.group(0)] = name

    figures: list[dict[str, Any]] = []
    if charts:
        try:
            figures = figure_refs.register_run(charts, figure_refs.scope_for(record))
        except Exception as exc:                               # noqa: BLE001
            lines.append(log("citadel", None, "figures-failed", f"{type(exc).__name__}: {exc}"))
    by_figure = {f["figure"]: f["placeholder"] for f in figures}
    for link, name in links.items():
        text = text.replace(link, by_figure.get(name, ""))
    text = IMAGE.sub("", text)                 # anything left is a path; never sent
    return text, figures


def _module_text(store: Any, slot: str) -> str:
    """The rendered module without its frontmatter, or "" when not written."""
    path = store.path(slot)
    if not path.is_file():
        return ""
    body = path.read_text(encoding="utf-8")
    return body.split("\n---\n", 1)[-1].strip() if body.startswith("---") else body.strip()
