"""LangGraph wiring.

    START -> argos -> (norwood | monty | entro | citadel) -> argos -> ... -> END

Argos is the only node that decides anything. Each specialist does its work,
appends a Finding to shared state, and hands control back. The loop is bounded
by GRAPH_MAX_STEPS so a confused orchestrator cannot spin forever.

Monty is special in two ways
----------------------------
1. It is not a chat node -- it runs its own four-node sub-graph
   (`core/monty_graph.py`): receive_input -> resolve_tasks -> execute_tasks ->
   return_result. Argos hands it an `event_id` and a list of registered
   function names with their parameters, not prose.
2. It has a retry contract. When Monty returns an error, Argos re-enters as a
   debugger, patches the parameters or the snippet, and re-sends -- at most
   MONTY_MAX_RETRIES times (default 2, so three attempts in total). Past that
   the error itself becomes the final answer.

A retry deliberately does **not** spend a delegation step. It is the same piece
of work being attempted again, not new work.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from . import (attribution, cache_policy, catalogue, dating, event_records,
               experiment as _experiment_mod, fact_sheet, figure_refs,
               plain_language, prerequisites, resolve, result_view, session,
               session_store, thesis_path, workflows)
from .config import graph_max_steps, monty_max_retries
from .logbook import log
from .monty_graph import run_monty
from .spec import flow_menu, list_flows, load_flow, valid_flow
from .state import Finding, GraphState, new_state

SPECIALISTS = ("norwood", "monty", "entro", "citadel")
MONTY_NAME = "Monty - Python expert"

# How many workflows one delegation to Monty may run.
#
# One, so the answer is about the question that was asked. Monty's sub-graph is
# still a batch runner and raising this restores batching with no other change;
# see the note in `monty_node` for why the cap lives there and not inside it.
MAX_TASKS_PER_DELEGATION = 1


def _agent(slug: str):
    """Import an agent lazily so a broken agents.md only breaks its own node."""
    from importlib import import_module

    module = import_module(f"agents.{slug}.agent")
    return module.build()


def _context_blob(state: GraphState) -> str:
    if not state.get("findings"):
        return "(nothing yet -- you are the first hop)"
    return "\n\n".join(
        f"### {f['agent']}\n{f['summary']}\n\n{f['detail']}" for f in state["findings"]
    )


def _active_context(state: GraphState) -> str:
    """The `active_context` the node schema names, as prose an agent can act on.

    Two halves with different jobs. The **run settings** shape how an answer is
    written and what it may rest on. The **session header** carries the facts
    the answer is about -- the AOI, its bounding box, the event window -- and
    those are not negotiable at all.

    The header is injected rather than offered because the failure it prevents
    is silent: an agent with no AOI does not stop and ask, it invents one, and
    the answer that comes back reads exactly like any other. `read_session`
    covers the rest of the record for whoever actually needs it.
    """
    parts = []
    persona = (state.get("persona") or "").strip()
    if persona:
        parts.append(f"persona: {persona}")
    if state.get("verified_sources_only"):
        parts.append(
            "verified sources only: ON -- do not rely on a figure whose "
            "citation does not resolve in its sources note. Name the "
            "unsupported key and leave the claim out rather than softening it."
        )

    blocks = []
    record = state.get("session")
    if record is not None and (header := record.header()):
        if state.get("announce"):
            header += ("\n- The AOI or the window was resolved on this turn. "
                       "Open the answer by stating both in one line, so a "
                       "wrong reading is corrected before it costs an analysis.")
        blocks.append(header)
    # The fact sheet, read from disk: what this session's stored results say,
    # crisp and structured, so no agent re-derives -- or fails to find -- a
    # number an earlier turn already fetched (core/fact_sheet.py).
    if facts := fact_sheet.read(record):
        body = facts.split("\n", 1)[1] if facts.startswith("# ") else facts
        blocks.append("### Session fact sheet\n" + body.strip())
    if parts:
        blocks.append("### Run settings\n" + "\n".join(f"- {p}" for p in parts))
    if not blocks:
        return ""
    return "## Active context\n" + "\n\n".join(blocks) + "\n\n"


# One backslash escape: the backslash and the character after it. Matched left
# to right, so an escaped backslash (two of them) is consumed as a pair and
# never mistaken for the start of another escape.
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_JSON_ESCAPES = set('"\\/bfnrtu')


def _repair_escapes(candidate: str) -> str:
    r"""Make the escapes JSON does not have parseable.

    Seen 2026-11-01 (Bangkok weather): the answer held "**Rainfall**\mThe",
    a newline the model wrote as \m. JSON has no \m, so both parses
    failed and the whole envelope -- answer, digest and next_step, with their
    \n escapes -- was shown to the user as the answer. A letter after a
    stray backslash is read as the newline it stood for; anything else (a
    markdown \* or \_) keeps its backslash as a literal one.
    """
    def fix(match: re.Match) -> str:
        char = match.group(1)
        if char in _JSON_ESCAPES:
            return match.group(0)
        return "\\n" if char.isalpha() else "\\\\" + char
    return _ESCAPE.sub(fix, candidate)


def _parse_decision(text: str) -> dict:
    """Argos returns JSON; be forgiving about fences and stray prose."""
    blob = text.strip()
    if blob.startswith("```"):
        blob = blob.split("```")[1]
        blob = blob[4:] if blob.startswith("json") else blob
    start, end = blob.find("{"), blob.rfind("}")
    if start != -1 and end > start:
        candidate = blob[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            # An answer is markdown, and a model writing a table puts real
            # newlines inside the JSON string rather than \\n. Strict JSON
            # rejects that, and the fallback below then returns the whole
            # envelope as the answer -- which is what a user sees. strict=False
            # allows control characters inside strings and nothing else.
            return json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_repair_escapes(candidate), strict=False)
        except json.JSONDecodeError:
            pass
    # A cut-off reply is not prose, and must not be shown as one.
    #
    # The fallback below is right when Argos genuinely answered in prose
    # instead of JSON. It is wrong when the reply is a fragment: a decision
    # that ran out of tokens mid-object came back with no closing brace, fell
    # through here, and the raw envelope -- `{"next": "norwood", "brief": ...`
    # -- was shown to the user as the answer. `BaseAgent.run` marks a
    # truncation in the text, which is what makes the two distinguishable.
    if "[TRUNCATED:" in text:
        return {
            "next": "finish",
            "truncated": True,
            # Plain language for the reader; the `truncated` line in the log
            # carries the developer's half (raise max_tokens, not effort).
            "answer": (
                "The answer was cut off before it was finished, so nothing "
                "was run on this turn. Please ask again."
            ),
        }

    # Unparseable -> treat the whole reply as the final answer rather than
    # looping on a malformed decision.
    return {"next": "finish", "answer": text}


# ---------------------------------------------------------------------------
# Resolver node -- runs once, before anything is routed
# ---------------------------------------------------------------------------
def _product_choice(record: Any, task: str) -> list[str]:
    """Read a flood-map product choice from the user's message (Python, not a
    model -- core/product_choice.py) and keep it with the session. Modules
    9-10 map the best-ranked product unless the user asked for another
    (user decision 2026-11-18). Returns the log lines."""
    from . import product_choice

    picked = product_choice.read(task)
    if picked is None:
        return []
    store = session_store.for_record(record)
    if picked == "best":
        if store is not None:
            product_choice.clear(store)
        record.flood_product = ""
        return [log("argos", None, "product-choice", "back to the best-ranked product")]
    if store is not None:
        product_choice.set_choice(store, picked, task)
    record.flood_product = picked
    return [log("argos", None, "product-choice",
                f"{product_choice.label(picked)} chosen by the user for the flood map")]


def resolve_node(state: GraphState) -> dict:
    """Settle the AOI and the time window, or end the turn asking about them.

    Runs before Argos on every turn. When the record already holds an AOI and
    the message names no new one, this is a single cheap model call that says
    "nothing changed" -- which is what makes a follow-up question inherit the
    session instead of re-deriving it.

    When the lookup is ambiguous the turn ends here, with a question as the
    answer and nothing delegated. A round trip is cheaper than an analysis over
    the wrong Boston, and far cheaper than one nobody notices was wrong.
    """
    record = state.get("session")
    if record is None:                     # direct run.py call, no session
        return {"announce": False}

    argos = _agent("argos")

    def extract(situation: str, message: str) -> str:
        # The contract is restated here and not left to the overlay alone.
        # Argos' system prompt is emphatic about the routing JSON, and an
        # overlay that asks for a different shape loses to it often enough to
        # matter -- the model answers with {"next": "citadel", ...} and the
        # reading comes back empty, which reads exactly like "no place named".
        # Every other turn in this file restates its contract in the message
        # for the same reason.
        return argos.run(
            f"## Session\n{situation}\n\n## Message\n{message}\n\n"
            "## Reply with ONLY this JSON object\n"
            '{"place": "the place named in THIS message, or \\"\\"", '
            '"approx_start": "YYYY-MM-DD or \\"\\"", '
            '"approx_end": "YYYY-MM-DD or \\"\\"", '
            '"change": "new|same|none", "about_event": false, '
            '"chosen_place_index": null, "chosen_event_index": null}\n\n'
            "You are NOT routing this message and NOT answering it. Do not "
            'reply with {"next": ...} -- that contract does not apply to this '
            "turn.",
            extra_system=argos.spec.resolver_prompt)

    before = (record.place.bbox, record.window.start, record.window.end)
    try:
        outcome = resolve.resolve(record, state["task"], extract)
        # Data fetched for one AOI and window says nothing about another, and
        # neither does an analysis run on it -- the thesis path starts over.
        if (record.place.bbox, record.window.start, record.window.end) != before:
            record.workflows_run = []
            record.flows_run = []
            record.flood_product = ""   # its file moves aside with the results
            # The stored results go aside with them, and the fact sheet is
            # rebuilt, so it cannot describe the old place under the new name.
            store = session_store.for_record(record)
            if store is not None:
                store.archive(f"AOI or window changed from {before}")
                fact_sheet.write(record, store)
    except Exception as exc:                                   # noqa: BLE001
        # Resolution is a convenience on top of the run, not a precondition of
        # it. A gazetteer outage should cost the answer its bounding box, not
        # the whole turn.
        return {
            "announce": False,
            "exchange_log": [log("argos", None, "resolve-failed",
                                 f"{type(exc).__name__}: {exc}")],
        }

    update: dict = {"announce": outcome["announce"], "session": record,
                    "exchange_log": outcome["log"]}
    # A confirmed AOI switch: the user's "yes" is not the request, the message
    # that prompted the question is -- run that on the new AOI.
    if outcome.get("task"):
        update["task"] = outcome["task"]
    if not outcome["asked"]:
        update["exchange_log"] = outcome["log"] + _product_choice(
            record, outcome.get("task") or state["task"])
    if outcome["asked"]:
        update.update({
            "route": "finish",
            "answer": outcome["answer"],
            "trace": ["resolve -> finish (asked the user)"],
        })
    elif outcome.get("about_event"):
        # The message asks about the event itself: list what the disaster
        # registers hold for it. Python looks them up and run_task appends the
        # names and links under the answer -- no model reads or restates them.
        records = event_records.for_record(record)
        found = [r["source"] for r in records if r["status"] == "matched"]
        update["event_records"] = records
        update["exchange_log"] = [*outcome["log"], log(
            "argos", None, "event-records",
            f"{len(found)} of {len(records)} sources matched"
            + (f": {', '.join(found)}" if found else ""))]
    return update


def _resolved(state: GraphState) -> str:
    """After resolution: ask the user, or get on with it."""
    return "finish" if state.get("route") == "finish" else "argos"


# ---------------------------------------------------------------------------
# Orchestrator node
# ---------------------------------------------------------------------------
def argos_node(state: GraphState) -> dict:
    """One orchestrator turn: route, debug-and-retry, or finish."""
    if state.get("monty_error"):
        return _argos_debug_turn(state)
    return _argos_route_turn(state)


def _analysis_menu() -> str:
    """The analyses each specialist can run, for Argos to choose between.

    Built from the flow cards, never hardcoded -- the same rule that keeps
    workflow names out of prompts. Only the name and `select_when` go in: the
    whole point of splitting card from knowledge is that eleven procedures
    never enter one prompt, and a routing decision needs the trigger, not the
    procedure. A specialist with no flows contributes nothing, so the section
    disappears entirely when none exist.
    """
    blocks = [f"{slug}:\n{menu}" for slug, menu in
              ((s, flow_menu(s)) for s in SPECIALISTS) if menu]
    if not blocks:
        return ""
    return (
        "## Analyses a specialist can run\n"
        + "\n\n".join(blocks)
        + "\n\nSet `flow` to one of these names when the specialist should run "
          "that analysis. Omit it for a general question answered from the "
          "session so far.\n\n"
    )


def _done_this_turn(state: GraphState) -> dict[str, list[str]]:
    """The workflows and flows THIS turn completed.

    The session record is updated only after the graph ends, so while Argus
    writes its answer the fetch it is answering about is not in the record --
    and the thesis path would recommend the step that just ran.
    """
    results = (state.get("monty_result") or {}).get("results") or {}
    return {
        "extra_workflows": [r.get("task") for r in results.values()
                            if r.get("status") == "success" and r.get("task")],
        "extra_flows": list(state.get("flows_run") or []),
    }


def _pending_interpretation(state: GraphState) -> tuple[str, str] | None:
    """`(specialist, flow)` that must read this run's Monty result, or None.

    A flow card naming `after_workflow` claims a workflow's output: when that
    workflow succeeded in this run and the flow has not run yet, the result is
    not an answer until the flow has read it. Argos still makes the routing
    decision -- this only tells it, and permits that one hop past "a specialist
    has answered".
    """
    results = ((state.get("monty_result") or {}).get("results") or {}).values()
    done = {r.get("task") for r in results if r.get("status") == "success"}
    if not done:
        return None
    already = set(state.get("flows_run") or [])
    for slug in SPECIALISTS:
        try:
            flows = list_flows(slug)
        except Exception:                                      # noqa: BLE001
            continue
        for flow in flows:
            claimed = set(flow.after_workflows or [flow.after_workflow]) - {""}
            if claimed & done and f"{slug}:{flow.name}" not in already:
                return slug, flow.name
    return None


def _argos_route_turn(state: GraphState) -> dict:
    argos = _agent("argos")
    steps = state.get("steps", 0)
    remaining = graph_max_steps() - steps
    record = state.get("session")
    done_now = _done_this_turn(state)

    # One specialist per user turn. Once a finding is in, Argos writes the
    # answer and *suggests* the next analysis rather than running it.
    #
    # This is the path the node schema always described -- User -> Argos ->
    # specialist -> Argos -> User -- and the graph was not holding to it: a
    # single question could spend four delegations before the user saw
    # anything, on a chain of hops they never agreed to. Stopping here makes
    # every step the user's decision, which is also what the cards mean when
    # they close by offering a choice.
    #
    # Retries are unaffected: a Monty failure goes through _argos_debug_turn,
    # which is the same work attempted again rather than new work.
    answering = bool(state.get("findings"))
    # The one exception to "one specialist per turn": a Monty result a flow
    # card claims (`after_workflow`) is interpreted before it is answered. The
    # table is computed by Monty and read by Entro; both are one request.
    pending = _pending_interpretation(state) if answering else None
    if pending:
        answering = False

    # Argos holds no tools, so it cannot fetch the catalogue note itself. The
    # graph fetches it and puts it in the request -- the vault stays the single
    # source of what a workflow is for, without the orchestrator becoming a
    # researcher. A vault it cannot read degrades to the signature list.
    catalogue_block, catalogue_problem = catalogue.for_routing()
    catalogue_lines = []
    if catalogue_problem:
        catalogue_lines.append(
            log("argos", None, "catalogue-partial",
                f"routing on signatures alone: {catalogue_problem}"))

    if answering:
        instruction = (
            "## Your move\n"
            "A specialist has answered. **Write the answer now.** Do not "
            "delegate again -- the user decides what happens next, and this "
            "turn ends with them.\n\n"
            "Reply with ONLY a JSON object:\n"
            '{"next": "finish", '
            '"answer": "the answer to the user, from the findings above", '
            '"digest": "what this session has established so far, 2-3 sentences", '
            '"next_step": {"label": "the one analysis that should follow, in '
            'the user\'s words", "flow": "its name from the lists above, or '
            'omit if none fits", "workflow": "a workflow that is NOT fetched '
            'yet, or omit", "why": "one sentence -- what it would add"}}'
            "\n\n`next_step` is a **suggestion**, not an instruction: it is "
            "shown to the user as something they may ask for. By default it "
            "is the NEXT step of the Thesis path above; choose another only "
            "when the user's question clearly calls for a different "
            "follow-up, and omit it only when the path is complete and "
            "nothing obvious follows. Never suggest an analysis already "
            "listed as run in the session header, or a workflow whose data is "
            "already fetched. If this run produced figures and you did not "
            "show one the user would likely want -- the NDVI class map after "
            "a land-cover fetch, the hyetograph after a weather fetch -- show "
            "it in the answer."
            + ("\n\n**A module was written this turn and is shown to the user in "
               "full under your answer.** Write `answer` as a covering note of two "
               "or three sentences that introduce it; do not restate its numbers, "
               "and do not place its map -- it is already in the module."
               if state.get("module_output") else "")
            + ("\n\n**The remote sensing expert's comments on this result are shown "
               "under your answer as written**, under the heading 'What this means "
               "for this flood event'. Write the answer about the data itself and do "
               "not repeat those comments."
               if state.get("appendix") else "")
        )
    elif pending:
        slug, flow_name = pending
        instruction = (
            "## Your move\n"
            f"Monty's result is in, and it is not an answer yet: the `{flow_name}` "
            f"analysis of `{slug}` interprets it. **Route to `{slug}` with flow "
            f"`{flow_name}` now.** Its inputs are handed to it with the brief; do "
            "not restate the numbers.\n\n"
            "Reply with ONLY a JSON object:\n"
            f'{{"next": "{slug}", "flow": "{flow_name}", '
            '"brief": "what the user asked, and what to explain about the result", '
            '"digest": "what this session has established so far, 2-3 sentences"}'
        )
    else:
        instruction = (
            f"## Budget\n{remaining} delegation(s) remaining, and **one "
            "specialist per turn** -- when their finding comes back you write "
            "the answer, you do not delegate again.\n\n"
            "Decide the next move. Reply with ONLY a JSON object:\n"
            '{"next": "norwood|monty|entro|citadel|finish", '
            '"brief": "what that agent must do, one paragraph", '
            '"flow": "an analysis name from the list above, or omit", '
            '"event_id": 42, '
            '"tasks": [{"function": "fetch_lulc_data", "params": {}}], '
            '"digest": "what this session has established so far, 2-3 sentences", '
            '"answer": "the final answer -- only when next == finish", '
            '"next_step": {"label": "...", "workflow": "...", "why": "..."}}\n\n'
            "`next_step` is only read when next == finish: use it to suggest "
            "a workflow from Prerequisite data when you answered without data "
            "the question needs, and otherwise the NEXT step of the Thesis "
            "path.\n\n"
            '`event_id` and `tasks` are required when next == "monty" and '
            "ignored otherwise, and both sit at the TOP level of the object -- "
            "not inside a wrapper. Monty executes code, it does not read "
            "prose: each task names a registered function, and its `params` "
            "keys are passed straight to that function's run(**params)."
        )

    routing_task = (
        f"{_active_context(state)}"
        f"## Task\n{state['task']}\n\n"
        f"## Findings so far\n{_context_blob(state)}\n\n"
        f"{_analysis_menu()}"
        f"{catalogue_block}"
        # The figures this experiment holds, by placeholder. Argus writes one
        # into `answer` and the console loads the image beside the text; given
        # the list, it never has to guess a name (core/figure_refs.py).
        # An experiment the message names counts too, for figures only.
        f"{figure_refs.prompt_block((state.get('experiment') or '').strip() or figure_refs.named_experiment(state['task']), run=state.get('run_figures'))}"
        # This run's tables, by placeholder (core/tables.py).
        f"{_tables_block(state)}"
        # Which of the weather and land-cover data this session already has,
        # so a question that needs missing data gets a suggestion to fetch it
        # rather than an answer from nothing (core/prerequisites.py).
        f"{prerequisites.prompt_block(state.get('session'))}"
        # The thesis outputs in order and how far this session is -- the same
        # list the console draws as a flowchart, so the step Argus recommends
        # is the step glowing on screen (core/thesis_path.py).
        f"{thesis_path.prompt_block(record, **done_now)}"
        f"{instruction}"
    )

    parsed = _parse_decision(argos.run(routing_task))
    nxt = parsed.get("next", "finish")
    if nxt not in SPECIALISTS:
        nxt = "finish"
    if remaining <= 0:
        nxt = "finish"

    stop_lines = []
    if pending and nxt not in (pending[0], "finish"):
        # Told to route the result to its interpreter and sent it elsewhere.
        # A second unrelated hop is still not allowed this turn.
        stop_lines.append(
            log("argos", None, "hand-back",
                f"asked for {nxt} while {pending[0]}:{pending[1]} was pending; "
                "answering instead"))
        nxt = "finish"
    if pending and nxt == pending[0] and not parsed.get("flow"):
        # The card that claims this result is the only flow it can mean; using
        # it is following the declaration, not guessing at a name. Logged so
        # the transcript shows it was not Argos' own choice.
        parsed["flow"] = pending[1]
        stop_lines.append(log("argos", None, "flow-pending",
                              f"{pending[0]}:{pending[1]} claims this result"))
    if answering and nxt != "finish":
        # Asked for the answer and told to delegate anyway. Carry it out as
        # the turn ending rather than as another hop, and say so -- a rule
        # enforced silently is one nobody can see was needed.
        stop_lines.append(
            log("argos", None, "hand-back",
                f"asked for {nxt} after a finding; answering instead"))
        nxt = "finish"

    # Argos chooses the analysis; this only checks the name exists, the same way
    # an unknown workflow name is rejected rather than invented. An unrecognised
    # one degrades to the specialist's general path, and is logged -- silently
    # dropping a choice Argos made would make the transcript a lie.
    chosen = parsed.get("flow") or ""
    flow = valid_flow(nxt, chosen) if nxt in SPECIALISTS else ""
    flow_lines = []
    if chosen and not flow:
        flow_lines.append(
            log("argos", None, "flow-unknown",
                f"{nxt} has no flow {chosen!r}; running its general path"))

    # The session's running account of itself, rewritten by the one node that
    # can see the whole run. Kept only when Argos actually wrote one -- an
    # omitted field must not erase what the last turn established.
    record = state.get("session")
    if record is not None and (digest := (parsed.get("digest") or "").strip()):
        record.digest = digest

    update: dict = {
        "route": nxt,
        # Always written, so a flow from an earlier hop cannot leak into this one.
        "flow": flow,
        "brief": parsed.get("brief", state["task"]),
        "answer": parsed.get("answer", "") if nxt == "finish" else "",
        "steps": steps + 1,
        "trace": [f"argos -> {nxt}" + (f" ({flow})" if flow else "")],
        "exchange_log": catalogue_lines + flow_lines + stop_lines + [
            log("argos", nxt, "delegate" if nxt != "finish" else "finish",
                (f"flow={flow} " if flow else "") + parsed.get("brief", "")[:80])],
    }
    # What Argos thinks should follow. Carried to the user as an offer, never
    # acted on here -- that is the whole point of stopping.
    suggestion = parsed.get("next_step")
    if nxt == "finish" and isinstance(suggestion, dict):
        label = str(suggestion.get("label") or "").strip()
        if label:
            asked_flow = str(suggestion.get("flow") or "")
            update["next_step"] = {
                "label": label,
                # Any specialist's flow: the AOI brief on the thesis path is
                # Norwood's, and checking Chanakya's alone dropped it.
                "flow": next((f for f in (valid_flow(s, asked_flow)
                                          for s in SPECIALISTS) if f), ""),
                # A registered workflow whose data this session lacks, or "".
                # Checked here, never trusted: data already fetched -- this
                # turn's fetch included -- is not suggested again, and an
                # invented name is dropped.
                "workflow": prerequisites.valid_workflow(
                    str(suggestion.get("workflow") or ""), record,
                    also_fetched=done_now["extra_workflows"]),
                "why": str(suggestion.get("why") or "").strip(),
            }
            update["exchange_log"] = update["exchange_log"] + [
                log("argos", None, "suggest", label[:80])]
    if nxt == "finish" and "next_step" not in update:
        # Argus made no offer. The thesis path makes one, so the user is never
        # left without the next step -- logged apart, so the transcript shows
        # the offer came from the path and not from Argus' reading.
        offer = thesis_path.suggestion(record, **done_now)
        if offer:
            update["next_step"] = offer
            update["exchange_log"] = update["exchange_log"] + [
                log("argos", None, "suggest-path", offer["label"][:80])]
    if nxt == "monty":
        # Fresh job: clear any retry state left over from an earlier one.
        update["monty_request"] = {
            "event_id": parsed.get("event_id"),
            "tasks": parsed.get("tasks") or [],
        }
        update["monty_debug"] = {}
        update["monty_attempts"] = 0
    return update


def _same_tasks(before: list, after: list) -> bool:
    """Is this patch the same task list that just failed?

    Compared by value with keys sorted, so a reordered dict is not mistaken for
    a change. Task ORDER is significant -- Monty runs them in order -- so the
    lists themselves are not sorted.
    """
    def shape(tasks):
        return [json.dumps(task, sort_keys=True, default=str) for task in tasks]

    return bool(before) and shape(before) == shape(after)


def _argos_debug_turn(state: GraphState) -> dict:
    """Monty failed. Argos becomes a debugger, or gives up."""
    error = state.get("monty_error") or {}
    attempts = state.get("monty_attempts", 0)
    retries_left = monty_max_retries() - (attempts - 1)

    if retries_left <= 0:
        return _argos_give_up(state, error, attempts)

    argos = _agent("argos")
    debug_task = (
        # The debugger needs the session's constants at least as badly as the
        # router does: a patched task list is rebuilt from the bounding box and
        # the window, and without them the diagnosis is correct and the fix is
        # impossible. This turn used to start at "## Original task".
        f"{_active_context(state)}"
        f"## Original task\n{state['task']}\n\n"
        f"## What you sent Monty\n"
        f"event_id = {json.dumps(error.get('event_id'))}\n"
        f"tasks = {json.dumps(error.get('tasks', []), indent=2)}\n\n"
        f"## What happened (attempt {attempts})\n"
        f"succeeded: {error.get('succeeded') or 'none'}\n"
        f"failed: {error.get('failed') or 'none'}\n\n"
        f"## First failure\n"
        f"task: {error.get('task')}\n"
        f"stage: {error.get('stage')}\n"
        f"error_type: {error.get('error_type')}\n"
        f"message: {error.get('message')}\n"
        f"function signature: {error.get('signature') or 'n/a'}\n"
        f"params supplied: {error.get('supplied') or 'n/a'}\n"
        f"hint: {error.get('hint') or 'n/a'}\n"
        f"traceback:\n{error.get('traceback') or '(none)'}\n\n"
        f"## Workflows Monty can run\n{workflows.catalogue()}\n\n"
        f"## Budget\n{retries_left} retry/retries left.\n\n"
        "Diagnose and fix. Reply with ONLY a JSON object:\n"
        '{"diagnosis": "one sentence -- what is actually wrong", '
        '"fix": "params|function|drop|abandon", '
        '"patched_tasks": [{"function": "name", "params": {}}], '
        '"brief": "what Monty should do on this attempt"}\n\n'
        "patched_tasks REPLACES the whole list -- include the tasks that "
        "already succeeded only if you want them run again, and drop any task "
        "that cannot be fixed.\n\n"
        'Use fix="abandon" when nothing in the task list can fix this -- a '
        "missing dependency, an unreachable service, a limit of the "
        "environment. Retrying an unchanged list cannot change the outcome, "
        "and abandoning reports the failure along with whatever did succeed. "
        'If fix="drop", patched_tasks MUST be shorter than the list above.'
    )

    parsed = _parse_decision(argos.run(debug_task,
                                       extra_system=argos.spec.debug_prompt))
    debug = {
        "diagnosis": parsed.get("diagnosis", ""),
        "fix": parsed.get("fix", "params"),
        "patched_tasks": parsed.get("patched_tasks") or [],
        "attempt_for": attempts + 1,
    }

    # Argos asked to stop. Honour it rather than spending the remaining
    # retries: the decision stays Argos', the graph only carries it out.
    if debug["fix"] == "abandon":
        line = log("argos", None, "abandon",
                   debug["diagnosis"] or "(no diagnosis given)")
        outcome = _argos_give_up({**state, "monty_debug": debug}, error, attempts)
        outcome["exchange_log"] = [line] + outcome["exchange_log"]
        return outcome

    lines = [
        log("argos", None, "debug", debug["diagnosis"] or "(no diagnosis given)"),
    ]

    # A patch identical to the list that just failed cannot produce a different
    # result unless the failure was transient, and one that claims to drop a
    # task while keeping every one of them contradicts itself. Neither is
    # blocked -- Argos owns the decision -- but neither is silent either, which
    # is how a wasted retry used to look exactly like a real one.
    previous = error.get("tasks") or []
    if _same_tasks(previous, debug["patched_tasks"]):
        lines.append(log("argos", None, "patch-noop",
                         f"{len(previous)} task(s) re-sent unchanged; only a "
                         "transient failure can pass this time"))
    elif debug["fix"] == "drop" and len(debug["patched_tasks"]) >= len(previous):
        lines.append(log("argos", None, "patch-contradiction",
                         f"fix=drop but {len(previous)} -> "
                         f"{len(debug['patched_tasks'])} task(s); nothing dropped"))

    lines.append(
        log("argos", "monty", "retry",
            f"attempt={attempts + 1} fix={debug['fix']} "
            f"retries_left={retries_left - 1}"))
    return {
        "route": "monty",
        "brief": parsed.get("brief", state.get("brief", "")),
        "monty_debug": debug,
        "trace": [f"argos [debugger] -> monty (retry {attempts})"],
        "exchange_log": lines,
        # `steps` is deliberately not incremented -- a retry is the same work.
    }


def _argos_give_up(state: GraphState, error: dict, attempts: int) -> dict:
    """Retries exhausted: one plain sentence for the reader, the full technical
    report for the log and the session folder.

    Both are assembled here, in Python -- no model narrates its own failure.
    The reader is a flood analyst, a member of the public or a mayor (the
    plain-language guardrail, Heart/Guardrails in the vault), so the answer
    names the workflow by what it does and says nothing about error types,
    task lists or tracebacks. The report keeps every one of those, so a
    developer loses nothing: `failure_report` in state, an event in the
    console's session log, and `sessions/<id>/errors/` on disk.
    """
    report = (
        f"FAILED after {attempts} attempt(s) -- Monty could not complete the job.\n\n"
        f"- event_id: {error.get('event_id')}\n"
        f"- failed task(s): {error.get('failed') or 'n/a'}\n"
        f"- succeeded task(s): {error.get('succeeded') or 'none'}\n"
        f"- first failure: {error.get('task')} -- {error.get('error_type')}: "
        f"{error.get('message')}\n"
        f"- stage: {error.get('stage')}\n"
        f"- tasks sent: {json.dumps(error.get('tasks', []))}\n"
        f"- last diagnosis: {(state.get('monty_debug') or {}).get('diagnosis') or 'n/a'}\n"
    )
    partial = (state.get("monty_result") or {}).get("results") or {}
    survivors = {k: v for k, v in partial.items() if v.get("status") == "success"}
    if survivors:
        # Partial success is still worth something -- do not throw it away just
        # because a sibling task failed.
        report += (f"\nResults that did succeed: {sorted(survivors)}\n"
                   f"{json.dumps(survivors, default=str)[:2000]}\n")
    if error.get("traceback"):
        report += f"\nTraceback (tail):\n{error['traceback']}\n"

    store = session_store.for_record(state.get("session"))
    if store is not None:
        try:
            from datetime import datetime, timezone

            errors = store.dir / "errors"
            errors.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (errors / f"{stamp}.md").write_text(report, encoding="utf-8")
        except OSError:
            pass                       # the report is still in state and the log

    failed = plain_language.workflow_name(str(error.get("task") or "the requested"))
    done = [plain_language.workflow_name(r.get("task") or k)
            for k, r in survivors.items()]
    answer = (f"There was an error while running the {failed} workflow. It did "
              f"not complete after {attempts} attempt(s), so this answer has none "
              "of its results.")
    if done:
        answer += (" The " + ", ".join(done) + " workflow"
                   + ("s" if len(done) > 1 else "") + " did complete, and "
                   + ("their" if len(done) > 1 else "its") + " results are kept.")
    answer += " You can ask again; if the error repeats, the details are in the session log."

    line = log("argos", None, "give-up",
               f"{attempts} attempts exhausted: {error.get('task')} "
               f"{error.get('error_type')}: {str(error.get('message') or '')[:160]}")
    return {
        "route": "finish",
        "answer": answer,
        "failure_report": report,
        "trace": [f"argos -> finish (monty failed x{attempts})"],
        "exchange_log": [line],
    }


def _route(state: GraphState) -> str:
    return state.get("route") or "finish"


# ---------------------------------------------------------------------------
# Specialist nodes
# ---------------------------------------------------------------------------
def _flow_overlay(slug: str, flow_name: str) -> tuple[str, list[str]]:
    """The flow card to overlay, and the log lines describing what happened.

    A missing or malformed flow is reported and the agent runs without an
    overlay, rather than failing the node: the specialist can still answer from
    its own prompt, and the log says the analysis was not applied.
    """
    if not flow_name:
        return "", []
    try:
        flow = load_flow(slug, flow_name)
    except (FileNotFoundError, ValueError) as exc:
        return "", [log(slug, None, "flow-missing", f"{flow_name}: {exc}")]

    line = log(slug, None, "flow", f"{flow.name} inputs={flow.inputs_required} "
                                   f"card={flow.card_sha}")
    return f"## Flow: {flow.title}\n\n{flow.card}", [line]


def _store(state: GraphState):
    """Where this run's results are kept: the named experiment, else the
    session's own folder (core/session_store.py), else None.

    The session folder is the default since 2026-10-15. Before it, a console
    run that named no experiment kept nothing, so a flow declaring the land
    cover, terrain and water it reads was served none of them -- however many
    turns earlier they had been fetched.
    """
    slug = (state.get("experiment") or "").strip()
    # An uncached run reads only what it fetched itself (core/cache_policy.py);
    # for_record returns the run's folder under that policy.
    if slug and not cache_policy.is_off():
        from .experiment import Experiment
        return Experiment(slug)
    return session_store.for_record(state.get("session"))


def _flow_inputs(state: GraphState, flow_name: str,
                 slug: str, store: Any = None) -> tuple[str, list[str]]:
    """The cached payloads a flow declares it consumes, as prompt text.

    This is the "the flow generates the inputs, the agent acts on them" half of
    the design. The agent is not asked to go and find its data: the card names
    what it needs, the graph reads exactly that from the experiment's inputs,
    and it arrives already in context.

    Two things are deliberate.

    **Only what the card declares.** A flow naming three payloads gets three,
    not everything the experiment holds, so a flow cannot quietly start
    depending on a layer it never declared.

    **Statistics only.** The same projection `read_monty_payload` applies:
    raster paths are stripped before an agent sees them, so "no raster access"
    is a property of what was handed over rather than an instruction that may
    or may not be followed.

    A declared payload that is missing is reported IN the prompt rather than
    silently omitted. An agent that cannot see a layer must know it is absent,
    or it writes around the gap as though the gap were a finding.
    """
    lines: list[str] = []
    if not flow_name:
        return "", lines

    try:
        spec = load_flow(slug, flow_name)
    except Exception:                                          # noqa: BLE001
        return "", lines                     # _flow_overlay already logged it
    if not spec.consumes:
        return "", lines

    from .experiment import stats_only
    from .projection import project, withheld

    # THIS run's Monty results, by workflow name. A flow that interprets a
    # result in the same run (`after_workflow`) reads it here first; the
    # store -- the named experiment, else the session folder -- serves the rest.
    this_run = {r.get("task"): r.get("output")
                for r in ((state.get("monty_result") or {}).get("results") or {}).values()
                if r.get("status") == "success" and r.get("task")}

    # No store is not a reason to stay silent: every declared input not found
    # below is reported ABSENT in the prompt.
    # A pipeline's agent step names its store (core/pipelines/base.py).
    try:
        run = store if store is not None else _store(state)
    except ValueError as exc:
        lines.append(log(slug, None, "feed-failed", str(exc)))
        run = None

    blocks: list[str] = []
    served: list[str] = []
    missing: list[str] = []
    for name in spec.consumes:
        if name in this_run:
            record = {"payload": this_run[name], "cached": "this run",
                      "summary": f"{name}, computed by Monty in this run"}
        else:
            record = run.cached_input(name) if run is not None else None
        if record is None:
            missing.append(name)
            continue
        # Two narrowings, in order. `stats_only` answers "no rasters";
        # `project` answers "of the statistics left, which does THIS consumer
        # declare it needs". The schema name comes from the card, so a flow
        # with no declared schema is served the statistics whole rather than
        # being silently starved.
        served_payload = stats_only(record.get("payload"))
        dropped = withheld(served_payload, name, spec.consumes_schema)
        served_payload = project(served_payload, name, spec.consumes_schema)
        if dropped:
            lines.append(log(slug, None, "projected",
                             f"{name}: withheld {dropped}"))
        payload = json.dumps(served_payload, default=str)
        header = "### " + name
        meta = "cached {} -- {}".format(record.get("cached", "?"),
                                        record.get("summary", ""))
        body = "```json\n" + payload + "\n```"
        blocks.append("\n\n".join([header, meta, body]))
        served.append(name)

    if missing:
        absent = "\n".join(
            "- `" + name + "` -- ABSENT. Do not infer it from the others; "
            "report it as missing." for name in missing)
        blocks.append(
            "### Payloads this flow declares but the experiment does not "
            "hold\n" + absent)

    lines.append(log(slug, None, "fed",
                     "{}: served {}{}".format(
                         flow_name, served or "none",
                         ", missing " + str(missing) if missing else "")))

    preamble = (
        "\n\n## Analysis inputs for this flow\n"
        "Statistics only -- raster paths are stripped, so nothing below can be "
        "read pixel-wise. These are the inputs the flow produced for you; do "
        "not go looking for others.\n\n")
    return preamble + "\n\n".join(blocks), lines


def make_specialist_node(slug: str):
    """Chat specialists: brief in, prose finding out.

    When Argos names a `flow`, that flow's card is overlaid on the agent's
    system prompt for this call only -- the same mechanism Argos uses to become
    a debugger. One card, never the whole catalogue: the evidence a flow needs
    is retrieved from the vault at the step that needs it.
    """

    def node(state: GraphState) -> dict:
        chosen = state.get("flow") or ""
        module = _flow_module(slug, chosen)
        if module:
            return _module_node(state, slug, chosen, module)
        agent = _agent(slug)
        overlay, lines = _flow_overlay(slug, chosen)

        # The flow feeds its agent. A card that declares `consumes` has its
        # payloads assembled here and put into the prompt, so the agent acts on
        # data the flow produced rather than spending turns fetching it: the
        # first attempt at this brief used its whole 8-turn budget on tool
        # calls and ended with "let me assemble the JSON" instead of the JSON.
        fed, feed_lines = _flow_inputs(state, chosen, slug)
        lines.extend(feed_lines)

        answer = agent.run(
            _active_context(state) + (state.get("brief") or state["task"]),
            context=_context_blob(state) + fed,
            extra_system=overlay)
        finding = Finding(
            agent=agent.spec.name,
            summary=answer.strip().split("\n")[0][:200],
            detail=answer,
        )
        # Persist the specialist's OWN words -- as a DELIVERABLE, not as an
        # input. Argos' answer is one JSON string field, so a 16-section brief
        # physically cannot pass through it: what reached the caller was five
        # paragraphs of prose about a document nobody could see. The artefact
        # is the specialist's text; the orchestrator's answer is the covering
        # note.
        #
        # `deliverables/`, deliberately not `inputs/`. Inputs are what an agent
        # is fed; deliverables are what a reader is shown. Putting a brief in
        # `inputs/` would let a later flow consume a previous flow's prose as
        # though it were measured data, which is how a summary of a summary
        # ends up in a thesis as a finding.
        # The session folder when no experiment is named (core/session_store).
        try:
            keeper = _store(state)
        except ValueError:
            keeper = None
        if keeper is not None:
            try:
                run = keeper
                run.create()
                path = run.write_deliverable(
                    chosen or slug, answer, agent=slug, flow=chosen,
                    brief=(state.get("brief") or "")[:400])
                lines.append(log(slug, None, "deliverable", str(path)))
            except Exception as exc:                           # noqa: BLE001
                lines.append(log(slug, None, "deliverable-failed",
                                 f"{chosen or slug}: {type(exc).__name__}: {exc}"))

        appendix = {}
        if overlay and _flow_appends(slug, chosen):
            # The comments go under Argus' answer as written; "no comment" is
            # an answer too, and leaves nothing to add (no forced points).
            text = answer.strip()
            if text and not NO_COMMENT.match(text):
                appendix = {"appendix": text}
            lines.append(log(slug, "argos", "comment", "none" if not appendix else
                             f"{len(text.splitlines())} line(s) under the answer"))
        return {
            **appendix,
            "findings": [finding],
            # `flow` is cleared by the next routing turn so it cannot leak into
            # a later hop, which loses the record of what produced the answer.
            # This is that record: appended when a card was actually applied,
            # not when one was merely selected.
            "flows_run": [f"{slug}:{chosen}"] if overlay else [],
            "trace": [f"{slug} -> argos"],
            "exchange_log": lines + [
                log(slug, "argos", "finding", finding["summary"])],
        }

    node.__name__ = f"{slug}_node"
    return node


NO_COMMENT = re.compile(r"^\W*no (further )?comments?\b", re.I)


def _flow_appends(slug: str, flow_name: str) -> bool:
    try:
        return load_flow(slug, flow_name).append_to_answer
    except Exception:                                          # noqa: BLE001
        return False


def _flow_module(slug: str, flow_name: str) -> str:
    """The module a flow writes (`module:` in its frontmatter), or ""."""
    if not flow_name:
        return ""
    try:
        return load_flow(slug, flow_name).module
    except Exception:                                          # noqa: BLE001
        return ""


def _module_node(state: GraphState, slug: str, flow_name: str, module: str) -> dict:
    """A flow that writes a module (Modules 9 and 10): the module's steps run
    on the session store -- Monty's tools, the remote sensing expert's
    hand-over, then this specialist's sections (core/pipelines/hev.py) -- and
    the rendered module is the finding. Argus writes the covering note;
    run_task appends the module under it."""
    from .pipelines import hev

    lines = [log(slug, None, "flow", f"{flow_name} writes {module}")]
    try:
        text, steps, figures = hev.run_for_session(state, module, state.get("persona") or "")
    except Exception as exc:                                   # noqa: BLE001
        text, steps, figures = "", [], []
        lines.append(log(slug, None, "module-failed", f"{module}: {type(exc).__name__}: {exc}"[:240]))
    lines += steps
    summary = (f"{module.replace('_', ' ')} written ({len(text.split())} words)" if text
               else f"{module.replace('_', ' ')} could not be written")
    finding = Finding(agent=_agent(slug).spec.name, summary=summary,
                      detail=text or "The module could not be written; see the session log.")
    return {
        "findings": [finding],
        "flows_run": [f"{slug}:{flow_name}"],
        "module_output": text,
        "run_figures": list(state.get("run_figures") or []) + figures,
        "trace": [f"{slug} -> argos"],
        "exchange_log": lines + [log(slug, "argos", "finding", summary)],
    }


def _with_output_dir(task: Any, rasters: Path) -> Any:
    """`task` with `output_dir` set to the session's rasters folder, when the
    workflow takes one and Argus did not set it. Anything else is unchanged.

    `"output_dir": null` is NOT set. Argus sends it, and testing for the key
    alone let it through: fetch_permanent_water wrote `None/42/water_class.tif`
    in the repo root, outside the served outputs, so the JRC map was never
    registered and never shown (user bug, 2026-11-17).
    """
    import inspect

    if not isinstance(task, dict):
        return task
    params = task.get("params") if isinstance(task.get("params"), dict) else {}
    entry, _ = workflows.resolve(str(task.get("function") or ""))
    if entry is None or params.get("output_dir") not in (None, ""):
        return task
    if "output_dir" not in inspect.signature(entry.fn).parameters:
        return task
    return {**task, "params": {**params, "output_dir": str(rasters)}}


def _table_folder(state: GraphState) -> Path:
    """Where a batch's tables are kept: the session (or run) folder, else
    outputs/tables/<run id> for a run with neither."""
    store = session_store.for_record(state.get("session"))
    if store is not None:
        return store.dir
    from .tables import OUTPUTS

    return OUTPUTS / "tables" / (cache_policy.run_id() or "no-run")


def _tables_block(state: GraphState) -> str:
    from .tables import prompt_block

    return prompt_block(state.get("run_tables"))


def _keepers(state: GraphState, sub_log: list[str]) -> list:
    """Where a Monty batch's results are kept.

    Twice when an experiment is named: in the experiment, for the thesis, and
    in the session folder, for the next turn's fact sheet. Without an
    experiment the session folder is the only keeper -- it used to be none,
    and a later turn found nothing to rank. Uncached, the run's own folder is
    the only keeper and the experiment's frozen inputs are left alone
    (core/cache_policy.py).
    """
    slug = (state.get("experiment") or "").strip()
    keepers: list = []
    if slug and cache_policy.is_off():
        sub_log.append(log("monty", None, "cache-state",
                           f"off: {slug} inputs not written; results go "
                           f"to runs/{cache_policy.run_id()}/"))
        slug = ""
    if slug:
        from .experiment import Experiment
        try:
            keepers.append(Experiment(slug))
        except ValueError as exc:
            sub_log.append(log("monty", None, "cache-failed", str(exc)))
    session_keeper = session_store.for_record(state.get("session"))
    if session_keeper is not None:
        keepers.append(session_keeper)
    return keepers


def monty_node(state: GraphState) -> dict:
    """Monty: event_id + task list in, result_1..result_N out.

    Runs its own sub-graph. Every path -- success, partial, failure -- comes
    back through here and edges to argos, so the retry decision is made in
    exactly one place.
    """
    attempt = state.get("monty_attempts", 0) + 1
    request = dict(state.get("monty_request") or {})
    debug = state.get("monty_debug") or {}

    # One workflow per delegation. Argos' prompt asks for one; this is what
    # makes it true, because a prompt is a request and a batch of three still
    # arrived -- asked about rainfall, Argos sent terrain, permanent water and
    # land cover alongside it, and the answer to the question was buried in
    # results nobody asked for.
    #
    # The cap belongs here, at the Argos -> Monty boundary, and NOT inside
    # `run_monty`. Monty's sub-graph is a batch runner by contract: numbered
    # results, failures isolated per task. Capping in there would have made
    # that machinery untestable -- `tests/test_weather_workflow.py` exercises
    # it with a deliberately broken four-task batch -- and would have confused
    # a policy about how much to ask for with the mechanics of running it.
    #
    # Deferred, never silently dropped: the surplus is logged and named in the
    # answer, so a reader can see what was not run and ask for it. That pairs
    # with the one-hop-per-turn rule -- the next step is a suggestion, not an
    # automatic second call.
    deferred: list[str] = []
    asked = list(request.get("tasks") or [])
    if len(asked) > MAX_TASKS_PER_DELEGATION:
        deferred = [t.get("function", "?") for t in asked[MAX_TASKS_PER_DELEGATION:]
                    if isinstance(t, dict)]
        request["tasks"] = asked[:MAX_TASKS_PER_DELEGATION]
        log("monty", None, "deferred",
            f"kept {MAX_TASKS_PER_DELEGATION} of {len(asked)} task(s); "
            f"not run this turn: {', '.join(deferred)}")

    # Rasters go into the session folder, beside the results that describe
    # them, unless Argus named a place itself. Only for a workflow that takes
    # `output_dir`: binding an unknown parameter would fail the task.
    keeper = session_store.for_record(state.get("session"))
    if keeper is not None:
        request["tasks"] = [_with_output_dir(t, keeper.rasters_dir)
                            for t in request.get("tasks") or []]

    sub = run_monty(request, attempt=attempt, debug=debug)
    results = sub.get("results") or {}
    tasks = sub.get("tasks") or request.get("tasks") or []
    sub_log = list(sub.get("exchange_log") or [])
    steps_taken = " | ".join(sub.get("execution_logs") or [])

    failed = [key for key, r in results.items() if r.get("status") != "success"]
    ok = [key for key, r in results.items() if r.get("status") == "success"]
    effective = {"event_id": request.get("event_id"), "tasks": tasks}
    payload = {
        "event_id": request.get("event_id"),
        "status": "error" if (failed or sub.get("error")) else "success",
        "results": results,
    }

    # A tool that RAN and failed is recorded too, beside the inputs, with the
    # parameters it was called with: the land-cover fallback may use
    # WorldCover only when Sentinel-2 is on record as having failed its
    # threshold for this AOI (mcdm.s2_failed_at_defaults). A wrong name or a
    # wrong parameter is a routing mistake, not an outcome, and is not kept.
    failed_runs = [r for r in results.values()
                   if r.get("status") != "success"
                   and (r.get("error") or {}).get("error_type") == "runtime_error"]
    if failed_runs:
        for run in _keepers(state, sub_log):
            for result in failed_runs:
                name = result.get("task") or "?"
                try:
                    run.create()
                    run.record_failure(workflows.cache_name(name), result["error"],
                                       params=result["error"].get("params"))
                    sub_log.append(log("monty", None, "failure-recorded",
                                       f"{name} -> {run.failures_dir}"))
                except Exception as exc:                   # noqa: BLE001
                    sub_log.append(log("monty", None, "cache-failed",
                                       f"{name} failure: {type(exc).__name__}: {exc}"))

    if payload["status"] == "success":
        # Persist every payload to the thesis cache, when this run belongs to
        # an experiment. Without it a console run produced the same numbers a
        # direct call did and then dropped them: the result lived in the log
        # and the run state and nowhere a later output could cite. For thesis
        # work that is backwards -- the run you would cite is the one that did
        # not persist.
        #
        # Failure here must never fail the run. The analysis succeeded; not
        # being able to write it down is a reportable problem, not a reason to
        # throw the answer away.
        keepers = _keepers(state, sub_log)
        for run in keepers:
            for key, result in results.items():
                if result.get("status") != "success":
                    continue
                name = result.get("task") or key
                try:
                    run.create()
                    # `fetch_terrain_data` caches as `terrain`, not
                    # `terrain_data`. The schema Norwood emits names its inputs
                    # `monty.lulc`, `monty.terrain`, `monty.permanent_water`,
                    # and a cache keyed differently means the agent looks for a
                    # payload that is there under another name. Two spellings
                    # of one thing is how the same fetch ends up cached twice.
                    # result_N is task N (1-based): two flood maps in one batch
                    # each keep their own product.
                    idx = int(key.split("_")[-1]) - 1 if key.startswith("result_") \
                        and key.split("_")[-1].isdigit() else -1
                    task_params = (tasks[idx].get("params") or {}) if (
                        0 <= idx < len(tasks) and tasks[idx].get("function") == name) else \
                        next((t.get("params") for t in tasks if t.get("function") == name), {})
                    # A flood map is stored per product (`flood_map_<id>`), so a
                    # map asked for on demand never overwrites the default one.
                    slug_name = workflows.cache_name(name, task_params)
                    run.cache_input(
                        slug_name, result.get("output"),
                        summary=f"{name} via {request.get('event_id')}, "
                                f"attempt {attempt}",
                        params=task_params)
                    sub_log.append(log("monty", None, "cached",
                                       f"{name} -> {run.inputs_dir}"))
                except Exception as exc:                   # noqa: BLE001
                    sub_log.append(log("monty", None, "cache-failed",
                                       f"{name}: {type(exc).__name__}: {exc}"))

        # The rasters this batch wrote, offered to Argus as `run-` placeholders
        # so the answer shows THIS run's image rather than pasting a file path
        # a browser cannot open (core/figure_refs.register_run). A failure
        # here costs the answer its images, never the run.
        run_figures: list[dict] = []
        scope = figure_refs.scope_for(state.get("session"))
        try:
            run_figures = figure_refs.register_run(results, scope)
        except Exception as exc:                               # noqa: BLE001
            sub_log.append(log("monty", None, "figures-failed",
                               f"{type(exc).__name__}: {exc}"))
        if run_figures:
            # Drawn now, in the background, so the images -- sea shaded, class
            # maps with their legends -- are ready by the time Argus's answer
            # names them. The run does not wait for them.
            figure_refs.prerender(run_figures)
            sub_log.append(log("monty", None, "figures",
                               ", ".join(f["name"] for f in run_figures)))

        # The batch's tables (core/tables.py): the MCDM matrix and ranking, the
        # sensitivity thresholds, class shares, road km, and the uncertainty
        # register of this batch -- kept as data beside the results, offered to
        # Argus as `<table - t-...>` placeholders the console draws.
        run_tables: list[dict] = []
        try:
            from . import tables as _tables

            folder = _table_folder(state)
            run_tables = [_tables.save(t, folder) for t in _tables.for_results(results)]
            if run_tables:
                sub_log.append(log("monty", None, "tables",
                                   ", ".join(t["key"] for t in run_tables)))
        except Exception as exc:                               # noqa: BLE001
            sub_log.append(log("monty", None, "tables-failed",
                               f"{type(exc).__name__}: {exc}"))

        # The fact sheet, rebuilt from what is now stored -- after the figures,
        # so it can list them -- so Argus answers this turn from it and the
        # next turn starts from it.
        session_keeper = session_store.for_record(state.get("session"))
        if session_keeper is not None and fact_sheet.write(
                state.get("session"), session_keeper):
            sub_log.append(log("monty", None, "facts", str(session_keeper.dir)))

        # Naming what was not run, in the finding itself. A deferral the reader
        # cannot see is indistinguishable from a workflow that was never
        # considered, and the whole point of running one at a time is that the
        # rest stay available to ask for.
        held_back = (f"\nnot run this turn (ask for them if wanted): "
                     f"{', '.join(deferred)}" if deferred else "")
        finding = Finding(
            agent=MONTY_NAME,
            summary=f"{len(ok)}/{len(results)} task(s) succeeded on attempt "
                    f"{attempt}: {', '.join(r['task'] for r in results.values())}"
                    + (f" (deferred: {', '.join(deferred)})" if deferred else ""),
            # The readable view, not a raw-JSON prefix: the thesis renderers
            # for the workflows that have one, condensed JSON for the rest, and
            # a visible marker on any cut (core/result_view.py). The 4000-char
            # prefix cut fetch_weather inside its hourly arrays, so the
            # rain, wind and cloud totals never reached the answer.
            detail=f"event_id: {request.get('event_id')}\n"
                   f"results:\n{result_view.results_view(results)}\n"
                   f"steps: {steps_taken}{held_back}",
        )
        return {
            "findings": [finding],
            "monty_result": payload,
            "monty_request": effective,
            "monty_error": {},          # cleared -> Argos routes normally
            "run_figures": run_figures,
            "run_tables": run_tables,
            "monty_attempts": attempt,
            "trace": [f"monty -> argos (ok, attempt {attempt})"],
            "exchange_log": sub_log,
        }

    # Failure: carry everything Argos needs to debug, and nothing it does not.
    # `error` is the FIRST failing task -- one fix at a time is the rule.
    err = sub.get("error") or {}
    error = {
        "stage": err.get("stage", "execute_tasks"),
        "error_type": err.get("error_type", "runtime_error"),
        "message": err.get("message", ""),
        "task": err.get("task"),
        "traceback": err.get("traceback", ""),
        "signature": err.get("signature"),
        "supplied": err.get("supplied"),
        "hint": err.get("hint"),
        "event_id": request.get("event_id"),
        "tasks": tasks,
        "failed": failed,
        "succeeded": [results[k]["task"] for k in ok],
        "attempt": attempt,
    }
    finding = Finding(
        agent=MONTY_NAME,
        summary=f"{len(ok)}/{len(results)} task(s) succeeded on attempt "
                f"{attempt}; {error['error_type']} on {error.get('task')}: "
                f"{error['message']}",
        detail=f"event_id: {request.get('event_id')}\nsteps: {steps_taken}",
    )
    return {
        "findings": [finding],
        "monty_result": payload,
        "monty_request": effective,
        "monty_error": error,
        "monty_attempts": attempt,
        "trace": [f"monty -> argos (error, attempt {attempt})"],
        "exchange_log": sub_log,
    }


# ---------------------------------------------------------------------------
# Build / run
# ---------------------------------------------------------------------------
def build_graph():
    builder = StateGraph(GraphState)
    builder.add_node("resolve", resolve_node)
    builder.add_node("argos", argos_node)
    builder.add_node("monty", monty_node)
    for slug in SPECIALISTS:
        if slug != "monty":
            builder.add_node(slug, make_specialist_node(slug))

    # Resolution comes before routing, and only ever runs once per turn: every
    # specialist edges back to argos, never back through here.
    builder.add_edge(START, "resolve")
    builder.add_conditional_edges("resolve", _resolved,
                                  {"argos": "argos", "finish": END})
    builder.add_conditional_edges(
        "argos",
        _route,
        {**{s: s for s in SPECIALISTS}, "finish": END},
    )
    for slug in SPECIALISTS:
        builder.add_edge(slug, "argos")   # every specialist reports back

    return builder.compile()


def run_task(task: str, persona: str = "",
             verified_sources_only: bool = False,
             session_id: str = "", experiment: str = "",
             cache: str = cache_policy.ON, run_id: str = "") -> GraphState:
    """Run one task through the graph, under one cache policy and run id.

    `cache` is `on` (default) or `off` (core/cache_policy.py). Under `off`
    every tool re-fetches, and the run reads and writes only `runs/<run_id>/`:
    the named experiment and the session folder are left untouched. `run_id`
    is minted when not given; it names that folder and every metric line.
    """
    with cache_policy.use(cache, run_id) as rid:
        log("run", None, "cache-state", f"{cache_policy.current()} run={rid}")
        return _run_task(task, persona=persona,
                         verified_sources_only=verified_sources_only,
                         session_id=session_id, experiment=experiment)


def _run_task(task: str, persona: str = "",
              verified_sources_only: bool = False,
              session_id: str = "", experiment: str = "") -> GraphState:
    """Run one task through the graph.

    `persona` and `verified_sources_only` are the caller's run settings -- the
    console sends them with every turn. They reach the specialists as the
    active context and change how an answer is written, not what it finds.

    `session_id` is what makes this a conversation rather than a series of
    unrelated questions. The record for that id is loaded before the run and
    saved after, so the AOI, the window and the digest survive to the next
    message. An empty id runs without one: `run.py` asks a single question and
    has nothing to carry forward.
    """
    # Published for the duration of the run so `read_monty_payload` can
    # reach it -- tools receive no state, and this is the same mechanism the
    # session record uses. Withdrawn in the `finally` below.
    run = None
    if experiment and cache_policy.is_off():
        # The experiment's frozen inputs are neither read nor written by an
        # uncached run; its own folder stands in (session_store.for_record).
        log("run", None, "cache-state",
            f"off: experiment {experiment} bypassed for runs/{cache_policy.run_id()}/")
    elif experiment:
        try:
            from .experiment import Experiment
            run = Experiment(experiment)
            run.create()
        except Exception as exc:                               # noqa: BLE001
            log("run", None, "experiment-missing",
                f"{experiment}: {type(exc).__name__}: {exc}")

    record = session.load(session_id) if session_id else None
    if record is not None:
        record.persona = persona
        record.verified_sources_only = verified_sources_only
        record.turns += 1
        # Tools receive no state, so the record is published for the duration
        # of the run and withdrawn afterwards -- `read_session` reads it here.
        session.set_current(record)
    # With no experiment named, the session's own folder is what
    # `read_monty_payload` reads: the results earlier turns stored.
    _experiment_mod.set_current(run or session_store.for_record(record))
    # Which stored payloads the agents read this turn, for the sources line.
    attribution.begin()

    graph = build_graph()
    log("run", None, "start", task)
    # recursion_limit guards LangGraph itself; GRAPH_MAX_STEPS guards Argos'
    # delegations and MONTY_MAX_RETRIES guards the retry loop on top of them.
    budget = graph_max_steps() * 2 + monty_max_retries() * 2 + 4
    try:
        final = graph.invoke(
            new_state(task, persona=persona,
                      verified_sources_only=verified_sources_only,
                      session_id=session_id, session=record,
                      experiment=experiment),
            {"recursion_limit": budget},
        )
    finally:
        _experiment_mod.set_current(None)
        if record is not None:
            # Saved even when the run raises: an AOI confirmed on a turn that
            # then failed is still settled, and asking for it twice is the kind
            # of thing that makes a console feel broken.
            session.save(record)
            session.set_current(None)

    # An uncached run's results live in runs/<run_id>/, not the session folder,
    # so marking them fetched would tell the next cached turn that data is
    # there when it is not. The AOI and window it settled are still kept.
    if record is not None and not cache_policy.is_off():
        record.flows_run = list(dict.fromkeys(
            [*record.flows_run, *(final.get("flows_run") or [])]))
        fetched = [r.get("task") for r in
                   ((final.get("monty_result") or {}).get("results") or {}).values()
                   if r.get("status") == "success" and r.get("task")]
        record.workflows_run = list(dict.fromkeys([*record.workflows_run, *fetched]))
        session.save(record)
    if record is not None:
        # Every turn ends with the fact sheet current: this turn's analyses
        # and turn count, on top of whatever Monty stored mid-run. Uncached,
        # it is the run's own sheet, runs/<run_id>/facts.md.
        fact_sheet.write(record, session_store.for_record(record))

    # The plain-language guardrail's safety net: internal agent and workflow
    # names out of everything the reader sees -- the answer and the offered
    # next step. Logged, because a scrub means the prompt rule was missed.
    answer, counts = plain_language.scrub(final.get("answer") or "")
    if final.get("module_output"):
        # A module written this turn goes under the covering note in full
        # (Modules 9 and 10); scrubbed like the answer it joins.
        module_text, more = plain_language.scrub(final["module_output"])
        for term, n in more.items():
            counts[term] = counts.get(term, 0) + n
        answer = answer.rstrip() + "\n\n" + module_text
    if final.get("appendix"):
        # A specialist's comments on this turn's data, as written (Norwood's
        # event_commentary), under their own heading.
        note, more = plain_language.scrub(final["appendix"])
        for term, n in more.items():
            counts[term] = counts.get(term, 0) + n
        answer = (answer.rstrip() + "\n\n### What this means for this flood event\n\n"
                  + note.strip())
    final["answer"] = answer
    offer = final.get("next_step") or {}
    for key in ("label", "why"):
        if offer.get(key):
            offer[key], more = plain_language.scrub(offer[key])
            for term, n in more.items():
                counts[term] = counts.get(term, 0) + n
    if counts:
        log("argos", None, "plain-language",
            ", ".join(f"{term} x{n}" for term, n in sorted(counts.items())))

    # Every agreement metric of every reference-map comparison this turn
    # rests on, written after every model has finished, so none can be left
    # out (user guardrail 2026-11-22, core/agreement_table.py).
    try:
        from . import agreement_table

        final["answer"] = agreement_table.append(final.get("answer") or "",
                                                 final.get("monty_result"))
    except Exception as exc:                                   # noqa: BLE001
        log("argos", None, "agreement-table-failed", f"{type(exc).__name__}: {exc}")

    # The sources the answer rests on, in the persona's register, written
    # after every model has finished so none can drop or reword one (user
    # decision 2026-11-01, core/attribution.py).
    try:
        zone = dating.store_zone(session_store.for_record(record)) if record else None
    except Exception:                                           # noqa: BLE001
        zone = None
    sources = attribution.render(attribution.used(final.get("monty_result")),
                                 persona, zone)
    if sources:
        # A payload's own source text can name a tool; the reader never sees one.
        sources, _ = plain_language.scrub(sources)
        final["answer"] = (final.get("answer") or "").rstrip() + sources

    # Event records go UNDER the answer, after every model has finished, so
    # nothing downstream can rewrite a link. Rendered here rather than in a
    # node because the answer is final only once the graph has ended.
    if final.get("event_records"):
        where = record.place.display if record is not None else ""
        when = record.window.line() if record is not None else ""
        final["answer"] = ((final.get("answer") or "").rstrip()
                           + "\n" + event_records.render(final["event_records"],
                                                         where, when))
    return final
