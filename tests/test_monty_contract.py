"""The routing prompt and the code that reads its reply must agree.

    python tests/test_monty_contract.py          # no model, no network

Argos is told what JSON to return in one place and the reply is unpacked in
another, and for a while those two places disagreed: the prompt asked for
`params: {event_id, dict_of_params}` while `monty_request` read `event_id` and
`tasks` off the top level. Argos obeyed the prompt, built a correct task list
inside `params`, and the graph threw it away -- `bad_payload: no tasks
supplied`, on a turn where nothing was actually wrong.

Nothing catches that except a live run, which is expensive and intermittent:
whichever spelling the model happens to favour decides whether the turn works.
So the prompt is captured here and checked against the keys the code reads.

The agent is replaced with a recorder. No model is called.
"""

from __future__ import annotations

import json
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.console_server import _status                        # noqa: E402
from core import graph                                        # noqa: E402
from core.workflows.sentinel import DEFAULT_INDICES, _INDEX_BANDS  # noqa: E402
from core.session import Place, SessionRecord, Window         # noqa: E402
from core.state import new_state                              # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


class Recorder:
    """Stands in for Argos: remembers the prompt, returns a fixed reply."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompt = ""
        self.spec = type("Spec", (), {"debug_prompt": "", "resolver_prompt": ""})()

    def run(self, task: str, context: str = "", extra_system: str = "") -> str:
        self.prompt = task
        return self.reply


def capture(reply: str, fn, state):
    """Run one Argos turn against a recorder, and hand back both sides."""
    original = graph._agent
    recorder = Recorder(reply)
    graph._agent = lambda slug: recorder
    try:
        # The turn runs first. Building the tuple inline would read
        # `recorder.prompt` before `fn` had set it -- left-to-right evaluation,
        # and every prompt assertion would pass against an empty string.
        update = fn(state)
    finally:
        graph._agent = original
    return recorder.prompt, update, recorder


def skeleton(prompt: str) -> dict:
    """The example JSON object out of a prompt, parsed.

    Found by brace matching rather than a regex: the object contains nested
    objects, and a lazy regex stops at the first inner brace.
    """
    start = prompt.find('{"next"')
    if start == -1:
        return {}
    depth = 0
    for i, ch in enumerate(prompt[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(prompt[start : i + 1])
    return {}


def session_state(**over):
    record = SessionRecord(session_id="contract")
    record.place = Place(display="Chennai, Tamil Nadu, India",
                         bbox=(80.15, 12.90, 80.35, 13.20), country="India")
    record.window = Window(start="2015-11-08", end="2015-12-09",
                           event="Flood in India (flood, Red)")
    record.confirmed = True
    state = new_state("How were the land conditions pre-event?",
                      persona="chanakya", session_id="contract", session=record)
    state.update(over)
    return state


# ---------------------------------------------------------------------------
# The routing turn
# ---------------------------------------------------------------------------
def test_routing_contract() -> None:
    reply = json.dumps({
        "next": "monty", "brief": "read land cover before the flood",
        "event_id": 42,
        "tasks": [{"function": "fetch_lulc_data",
                   "params": {"bbox": [80.15, 12.9, 80.35, 13.2]}}],
        "digest": "Rainfall totals retrieved.",
    })
    prompt, update, _ = capture(reply, graph._argos_route_turn, session_state())
    shape = skeleton(prompt)

    check("the routing prompt carries an example object", bool(shape),
          f"{sorted(shape)}")
    # The two keys monty_node cannot run without. Read straight off `parsed`
    # in _argos_route_turn, so they have to be top-level in what is asked for.
    for key in ("event_id", "tasks"):
        check(f"the prompt asks for {key!r} at the top level", key in shape,
              f"top-level keys: {sorted(shape)}")
    check("the prompt does not ask for a `params` wrapper instead",
          "params" not in shape,
          "a wrapper puts the task list where monty_request does not look")
    check("the example task names a function and its params",
          isinstance(shape.get("tasks"), list) and shape["tasks"]
          and set(shape["tasks"][0]) == {"function", "params"},
          str(shape.get("tasks")))

    # The other half: a reply in the documented shape must actually arrive.
    request = update.get("monty_request") or {}
    check("a reply in that shape reaches Monty with its tasks",
          request.get("tasks") and request["tasks"][0]["function"]
          == "fetch_lulc_data",
          f"tasks={request.get('tasks')}")
    check("and with its event_id", request.get("event_id") == 42,
          repr(request.get("event_id")))


def test_routing_sees_the_session() -> None:
    prompt, _, _ = capture('{"next": "finish", "answer": "done"}',
                           graph._argos_route_turn, session_state())
    check("the routing turn is given the AOI", "Chennai" in prompt)
    check("the routing turn is given the bounding box", "80.1500" in prompt)
    check("the routing turn is given the time window", "2015-11-08" in prompt)


# ---------------------------------------------------------------------------
# The debug turn
# ---------------------------------------------------------------------------
def failed_state():
    return session_state(
        monty_attempts=1,
        monty_error={
            "stage": "execute_tasks", "error_type": "runtime_error",
            "message": "no scene cleared the cloud threshold",
            "task": "fetch_lulc_data", "event_id": 42,
            "tasks": [{"function": "fetch_lulc_data", "params": {}}],
            "failed": ["result_1"], "succeeded": [], "attempt": 1,
        })


def test_debug_sees_the_session() -> None:
    """The defect this turn had: a correct diagnosis and no way to act on it.

    Argos diagnosed a live failure as "the bounding box and time window should
    have come from the session constants" -- and it was right, because this
    prompt did not carry them. A debugger that cannot see the AOI cannot
    rebuild a task list that needs one.
    """
    reply = json.dumps({
        "diagnosis": "the cloud threshold is too strict for this window",
        "fix": "params",
        "patched_tasks": [{"function": "fetch_lulc_data",
                           "params": {"max_scene_cloud": 0.4}}],
        "brief": "retry with a looser cloud threshold",
    })
    prompt, update, _ = capture(reply, graph._argos_debug_turn, failed_state())

    check("the debug turn is given the AOI", "Chennai" in prompt,
          "without it a patched task list cannot be rebuilt")
    check("the debug turn is given the bounding box", "80.1500" in prompt)
    check("the debug turn is given the time window", "2015-11-08" in prompt)
    check("the debug turn still carries the failure it is fixing",
          "no scene cleared the cloud threshold" in prompt)
    check("a patch in the documented shape is accepted",
          (update.get("monty_debug") or {}).get("patched_tasks"),
          str((update.get("monty_debug") or {}).get("patched_tasks")))


def test_debug_contract_matches_what_is_read() -> None:
    """The debug reply is unpacked by name too, so it gets the same check."""
    reply = '{"diagnosis": "d", "fix": "params", "patched_tasks": [], "brief": "b"}'
    prompt, _, _ = capture(reply, graph._argos_debug_turn, failed_state())
    for key in ("diagnosis", "fix", "patched_tasks", "brief"):
        check(f"the debug prompt asks for {key!r}", f'"{key}"' in prompt)


# ---------------------------------------------------------------------------
# What the catalogue promises, the workflow has to accept
# ---------------------------------------------------------------------------
def test_index_names_are_case_insensitive() -> None:
    """The one text about this workflow that reaches a prompt names the indices
    in capitals. Rejecting capitals punished a caller for reading it."""
    from core.workflows import catalogue

    advertised = next(line for line in catalogue().splitlines()
                      if "fetch_lulc_data" in line)
    for name in ("NDVI", "NDBI", "BUI", "NDMI", "NMDI", "MNDWI"):
        check(f"the catalogue advertises {name}", name in advertised)
    check("the catalogue says the case does not matter",
          "in any case" in advertised)

    for probe, want in ((["NDVI", "BUI"], ["ndvi", "bui"]),
                        ([" MNDWI "], ["mndwi"]),
                        (None, DEFAULT_INDICES)):
        folded = [str(i).strip().lower() for i in (probe or DEFAULT_INDICES)]
        check(f"{probe!r} resolves to a known index",
              folded == want and not [i for i in folded if i not in _INDEX_BANDS],
              str(folded))
    check("a genuinely unknown index is still rejected",
          "ndxx" not in _INDEX_BANDS)


def test_raster_workflows_share_one_output_default() -> None:
    """Every workflow that writes a raster writes to the same place.

    Three defaults drifting apart is how a run ends up scattered across
    directories nobody gitignored. `output_dir` stays a parameter -- a caller
    may name somewhere else -- but the default is one value, and .gitignore
    knows that value.
    """
    import inspect

    from core.workflows import sentinel, terrain, water

    defaults = {}
    for module, fn in (("sentinel", sentinel.fetch_lulc_data),
                       ("terrain", terrain.fetch_terrain_data),
                       ("water", water.fetch_permanent_water)):
        defaults[module] = inspect.signature(fn).parameters["output_dir"].default

    check("every raster workflow shares one output_dir default",
          len(set(defaults.values())) == 1, str(defaults))
    check("that default is `outputs`",
          set(defaults.values()) == {"outputs"}, str(set(defaults.values())))

    ignored = pathlib.Path(__file__).resolve().parent.parent / ".gitignore"
    check("and .gitignore covers it",
          "outputs/" in ignored.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# How a run is reported
# ---------------------------------------------------------------------------
def test_status_reads_the_outcome_not_the_transcript() -> None:
    """A first attempt is allowed to fail -- that is what the retry is for.

    Scanning the whole log for "FAILED" counted superseded attempts as damage
    and badged a run that recovered 4/4 as degraded.
    """
    healed = {
        "exchange_log": ["... FAILED result_2 runtime_error ...",
                         "... PARTIAL attempt=1 3/4 ok ...",
                         "... OK attempt=2 4/4 tasks ok"],
        "monty_result": {"status": "success", "results": {
            f"result_{i}": {"status": "success"} for i in range(1, 5)}},
    }
    still_broken = {
        "exchange_log": ["... PARTIAL attempt=1 3/4 ok ..."],
        "monty_result": {"status": "error", "results": {
            "result_1": {"status": "success"},
            "result_2": {"status": "error"}}},
    }
    gave_up = {"answer": "There was an error while running the event weather "
                         "workflow.", "failure_report": "FAILED after 3 attempt(s)",
               "monty_result": {}}
    untouched = {"exchange_log": ["... finding ..."], "monty_result": {}}

    check("a run that self-healed on retry reports ok",
          _status(healed) == "ok", _status(healed))
    check("a run still carrying a failed task reports partial",
          _status(still_broken) == "partial", _status(still_broken))
    check("a run that gave up reports error",
          _status(gave_up) == "error", _status(gave_up))
    check("a run that never called Monty reports ok",
          _status(untouched) == "ok", _status(untouched))


# ---------------------------------------------------------------------------
# One specialist per turn
# ---------------------------------------------------------------------------
def test_the_turn_ends_when_a_specialist_answers() -> None:
    """The path the node schema always described: User -> Argos -> one
    specialist -> Argos -> User.

    The graph did not hold to it -- a single question could spend four
    delegations on a chain of hops the user never agreed to, and they saw
    nothing until it finished. Now the next hop leaves as a suggestion.
    """
    first = json.dumps({"next": "citadel", "brief": "run the sewage flow",
                        "digest": "d"})
    _, update, _ = capture(first, graph._argos_route_turn, session_state())
    check("with no finding yet, a delegation still goes ahead",
          update["route"] == "citadel", update["route"])

    answered = session_state(findings=[{"agent": "Norwood", "summary": "s",
                                        "detail": "d"}])
    again = json.dumps({
        "next": "citadel", "brief": "keep going", "digest": "d",
        "answer": "here is the answer",
        "next_step": {"label": "check the sewage risk",
                      "flow": "human_health_risk",
                      "why": "it would quantify the health side"}})
    prompt, update, _ = capture(again, graph._argos_route_turn, answered)

    check("after a finding, a second delegation is refused",
          update["route"] == "finish", update["route"])
    check("and the answer is returned instead",
          update["answer"] == "here is the answer")
    check("the refusal is logged, not silent",
          any("hand-back" in line for line in update["exchange_log"]))
    check("the next hop comes back as a suggestion",
          (update.get("next_step") or {}).get("label") == "check the sewage risk",
          str(update.get("next_step")))
    check("its flow name is validated like any other",
          update["next_step"]["flow"] == "human_health_risk")
    check("the answering prompt asks for the answer, not a route",
          "Do not delegate again" in prompt and "next_step" in prompt)


def test_a_suggested_flow_that_does_not_exist_is_dropped() -> None:
    """Same rule as routing: a name is checked, never invented."""
    answered = session_state(findings=[{"agent": "Norwood", "summary": "s",
                                        "detail": "d"}])
    reply = json.dumps({"next": "finish", "answer": "a", "digest": "d",
                        "next_step": {"label": "look at the tides",
                                      "flow": "invented_flow", "why": "w"}})
    _, update, _ = capture(reply, graph._argos_route_turn, answered)
    check("an invented flow name is dropped from the suggestion",
          update["next_step"]["flow"] == "", str(update["next_step"]))
    check("but the suggestion itself survives, since the label is real",
          update["next_step"]["label"] == "look at the tides")


def test_a_delegation_names_who_takes_over() -> None:
    """The active indicator follows the hand-off, not the logger.

    A delegation is logged by the agent handing off, so attributing it to the
    source alone lights up Argos while Norwood is the one working.
    """
    from api.console_server import _to_event

    out = _to_event("05:10:45  argos  -> norwood delegate  look at the imagery", 0)
    back = _to_event("05:10:46  norwood -> argos  finding   verdict", 1)
    internal = _to_event("05:10:47  argos  .        suggest   check sewage", 2)

    check("a delegation names the specialist taking over",
          out["agent"] == "argos" and out.get("routedTo") == "norwood",
          f"agent={out['agent']} routedTo={out.get('routedTo')}")
    check("a finding hands control back to Argos",
          back.get("routedTo") == "argos", str(back.get("routedTo")))
    check("an internal step routes to nobody",
          "routedTo" not in internal, str(internal.get("routedTo")))


def main() -> int:
    test_routing_contract()
    test_routing_sees_the_session()
    test_debug_sees_the_session()
    test_debug_contract_matches_what_is_read()
    test_index_names_are_case_insensitive()
    test_raster_workflows_share_one_output_default()
    test_status_reads_the_outcome_not_the_transcript()
    test_the_turn_ends_when_a_specialist_answers()
    test_a_suggested_flow_that_does_not_exist_is_dropped()
    test_a_delegation_names_who_takes_over()

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
