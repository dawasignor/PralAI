"""The single state object that flows through the LangGraph."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

AgentSlug = Literal["argos", "norwood", "monty", "entro", "citadel"]


class Finding(TypedDict):
    """One specialist's contribution, appended to the shared state."""

    agent: str
    summary: str
    detail: str


class GraphState(TypedDict, total=False):
    """State shared by every node.

    `findings` and `trace` use operator.add so concurrent (fan-out) branches
    merge instead of clobbering each other.
    """

    task: str                                     # the user's original request
    # The session this turn belongs to. `session` is a core.session.SessionRecord
    # holding the constants -- AOI, window, demographics -- plus a digest of
    # what the agents have delivered. It is loaded before the graph runs and
    # saved after, so a follow-up question inherits both instead of re-deriving
    # them. `announce` is true only on the turn a new AOI or window was
    # resolved, which is the one turn the answer has to open by stating them.
    session_id: str
    session: Any
    announce: bool
    # Run settings the caller chose. They shape how an answer is written and
    # what it may rest on -- never what the analysis finds.
    persona: str                                  # voice: flood_bot | chanakya | norwood
    verified_sources_only: bool                   # refuse unverified citations
    route: str                                    # next hop chosen by Argos
    flow: str                                     # analysis for the NEXT hop, "" for none
    flows_run: Annotated[list[str], operator.add]  # analyses actually applied, in order
    brief: str                                    # Argos' instruction to that specialist
    findings: Annotated[list[Finding], operator.add]
    trace: Annotated[list[str], operator.add]     # human-readable hop log
    exchange_log: Annotated[list[str], operator.add]   # one line per exchange
    steps: int                                    # delegations spent, guards the loop
    answer: str                                   # final synthesis from Argos
    # What Argos thinks should follow, offered to the user rather than run.
    # One specialist per turn: the next hop is the user's decision, so it
    # leaves here as a suggestion and comes back only if they ask for it.
    next_step: dict[str, Any]
    context: dict[str, Any]                       # free-form scratch space

    # -- Monty's execution channel -----------------------------------------
    # Monty is the only specialist Argos hands a structured payload to and the
    # only one with a retry contract, so its channels are explicit rather than
    # buried in `context`. A retry does NOT spend a delegation step -- it is the
    # same piece of work, attempted again.
    monty_request: dict[str, Any]                 # {"event_id", "tasks": [...]}
    monty_result: dict[str, Any]                  # {"status", "results", "event_id"}
    monty_error: dict[str, Any]                   # last failure, {} when clean
    monty_debug: dict[str, Any]                   # Argos' patch for the next attempt
    monty_attempts: int                           # attempts made, 0 = never called
    # Which thesis experiment this run belongs to, as a slug under
    # `experiments/`. Set, every workflow payload Monty produces is written to
    # that event's input cache, so a run through the console leaves the same
    # record a direct call does. Empty means no caching -- an ordinary question
    # should not write to a thesis cache.
    experiment: str
    # What the disaster registers hold for the event, when the message asked
    # about the event itself (core/event_records.py). Names and links only;
    # run_task appends them under the answer.
    event_records: list[dict[str, Any]]
    # Rasters Monty wrote in this run, registered as `run-region-year-figure`
    # placeholders (core/figure_refs.register_run) for Argus to place.
    run_figures: list[dict[str, Any]]
    # The tables this run's results yield (core/tables.py), as `<table - t-...>`
    # placeholders: data the console draws, never numbers retyped by a model.
    run_tables: list[dict[str, Any]]
    # The technical account of a run that gave up: attempts, error, traceback.
    # Never shown in the answer -- the reader gets one plain sentence -- but
    # kept for the session log and the session folder. Non-empty means the
    # run ended in failure.
    failure_report: str
    # A module a flow wrote this turn (Modules 9 and 10 in chat, user decision
    # 2026-11-02), rendered from its vault template. Too long for Argus' JSON
    # answer, so run_task appends it under Argus' covering note.
    module_output: str
    # A specialist's comments placed under the answer as written (a flow with
    # `append_to_answer`), e.g. Norwood on the data just fetched. "" for none.
    appendix: str


def new_state(task: str, persona: str = "",
              verified_sources_only: bool = False,
              session_id: str = "", session: Any = None,
              experiment: str = "") -> GraphState:
    return GraphState(
        task=task,
        session_id=session_id,
        session=session,
        experiment=experiment,
        announce=False,
        persona=persona,
        verified_sources_only=verified_sources_only,
        route="",
        flow="",
        flows_run=[],
        brief="",
        findings=[],
        trace=[],
        exchange_log=[],
        steps=0,
        answer="",
        next_step={},
        context={},
        module_output="",
        appendix="",
        monty_request={},
        monty_result={},
        monty_error={},
        monty_debug={},
        monty_attempts=0,
        failure_report="",
    )
