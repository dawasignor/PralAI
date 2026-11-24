"""The tool loop's turn budget. Offline -- no provider, no network.

    python tests/test_agent_loop.py

The behaviour under test is easy to break by accident and invisible when it
breaks: an agent that spends its last turn calling a tool produces an empty
finding, and the graph carries that on as though it were an answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import base_agent                                 # noqa: E402
from core.llm import LLMResponse, ToolCall                  # noqa: E402

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


class FakeLLM:
    """Answers with a tool call every time it is offered a tool.

    That is the pathological case -- an agent that would happily retrieve
    forever -- and it is what Norwood did in the live run.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, *, system, messages, tools=None, **kw):
        self.calls.append({"tools_offered": len(tools or []),
                           "messages": len(messages)})
        if tools:
            return LLMResponse(
                text="",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id=f"t{len(self.calls)}",
                                     name="read_note", input={"ref": "X"})],
            )
        return LLMResponse(text="Here is my finding.", stop_reason="end_turn")

    def append_assistant_turn(self, messages, response):
        messages.append({"role": "assistant", "content": "(tool call)"})

    def append_tool_results(self, messages, pairs):
        for _call, _result in pairs:
            messages.append({"role": "user", "content": "(tool result)"})

    def describe(self) -> str:
        return "fake/model"


def make_agent(max_turns: int, tools: list[str]) -> tuple[base_agent.BaseAgent, FakeLLM]:
    """An agent with a fake model and no real spec loading."""
    agent = base_agent.BaseAgent.__new__(base_agent.BaseAgent)
    agent.slug = "fake"
    agent.spec = type("Spec", (), {
        "max_turns": max_turns, "name": "Fake", "system_prompt": "be useful",
        "tools": tools, "max_tokens": 8000, "effort": "high",
        "prompt_shas": {"system": "0" * 12},
    })()
    agent.llm = FakeLLM()
    agent.tool_defs = [{"name": t} for t in tools]
    agent.system_prompt = lambda extra="": "be useful"
    return agent, agent.llm


def check_last_turn_reserved() -> None:
    """The final turn must be offered no tools, so it has to answer."""
    agent, llm = make_agent(max_turns=4, tools=["read_note"])
    base_agent.memory.append_entry = lambda *a, **k: None      # no journal writes
    base_agent.tools.execute = lambda name, payload: '{"ok": true}'

    answer = agent.run("what do the notes say?")
    offered = [c["tools_offered"] for c in llm.calls]
    print(f"  turns used     : {len(llm.calls)} of 4")
    print(f"  tools offered  : {offered}")

    if len(llm.calls) != 4:
        fail(f"expected the full budget to be used, got {len(llm.calls)} turns")
    if offered[-1] != 0:
        fail(f"the last turn was offered {offered[-1]} tool(s); it must be reserved")
    if any(o == 0 for o in offered[:-1]):
        fail(f"a non-final turn was denied tools: {offered}")

    if "Here is my finding." not in answer:
        fail("the reserved turn did not produce an answer")
    else:
        print("  answer produced: yes")

    if "retrieval budget ran out" not in answer:
        fail("an answer cut short was not marked as resting on incomplete evidence")
    else:
        print("  marked incomplete: yes")

    if "stopped after" in answer:
        fail("the old exhaustion marker fired even though the loop answered")


def check_normal_stop_is_not_flagged() -> None:
    """An agent that finishes early must not be labelled incomplete."""
    agent, llm = make_agent(max_turns=6, tools=[])             # no tools at all
    base_agent.memory.append_entry = lambda *a, **k: None

    answer = agent.run("just answer")
    print(f"  turns used     : {len(llm.calls)} of 6")

    if len(llm.calls) != 1:
        fail(f"an agent with nothing to call should stop after 1 turn, used {len(llm.calls)}")
    if "retrieval budget ran out" in answer:
        fail("a considered answer was wrongly marked incomplete")
    else:
        print("  marked incomplete: no (correct)")


def check_single_turn_budget() -> None:
    """max_turns=1 has no retrieval to cut short, so no warning."""
    agent, llm = make_agent(max_turns=1, tools=["read_note"])
    base_agent.memory.append_entry = lambda *a, **k: None

    answer = agent.run("one shot")
    print(f"  tools offered  : {[c['tools_offered'] for c in llm.calls]}")
    if "retrieval budget ran out" in answer:
        fail("a one-turn agent was wrongly warned about lost retrieval")
    else:
        print("  marked incomplete: no (correct)")


class TruncatingLLM:
    """Stops mid-sentence because it ran out of room.

    The reply is a fragment, and every other stop reason means the model chose
    to stop -- so a caller that does not check cannot tell the two apart. That
    is how a cut-off routing decision reached the user as raw JSON.
    """

    def complete(self, *, system, messages, tools=None, **kw):
        return LLMResponse(
            text='{"next": "norwood", "brief": "identify what satellite',
            stop_reason="max_tokens")

    def append_assistant_turn(self, messages, response):
        messages.append({"role": "assistant", "content": ""})

    def append_tool_results(self, messages, pairs):
        pass

    def describe(self) -> str:
        return "fake/model"


def check_truncation_is_marked() -> None:
    """A reply cut off by the token ceiling says so, in the text itself."""
    agent, _ = make_agent(max_turns=6, tools=[])
    agent.llm = TruncatingLLM()
    out = agent.run("route this")
    limit = str(agent.spec.max_tokens)

    print(f"  returned: {out[:60]!r}")
    if "[TRUNCATED:" not in out:
        fail("a reply cut off at max_tokens is not marked as incomplete")
    else:
        print("  ok  the fragment is marked [TRUNCATED: ...]")
    if limit not in out:
        fail(f"the marker does not name the limit that was hit ({limit})")
    else:
        print("  ok  it names the limit that was hit")


def check_parser_refuses_a_truncated_decision() -> None:
    """The graph must not show a fragment to the user as an answer."""
    from core.graph import _parse_decision

    cut = ('{"next": "norwood", "brief": "identify what satellite'
           "\n\n[TRUNCATED: the reply hit the 8000-token limit and stopped "
           "mid-sentence. Treat it as incomplete.]")
    prose = "I cannot route this without an area of interest."

    a = _parse_decision(cut)
    b = _parse_decision(prose)
    print(f"  truncated -> next={a['next']} truncated={a.get('truncated')}")
    print(f"  prose     -> next={b['next']} truncated={b.get('truncated', False)}")

    if not a.get("truncated"):
        fail("a truncated decision is not flagged")
    elif '"next"' in a["answer"] or "brief" in a["answer"]:
        fail("the raw envelope is still being shown as the answer")
    else:
        print("  ok  the fragment is replaced with an explanation")
    if b.get("truncated"):
        fail("deliberate prose is being mistaken for a truncation")
    elif b["answer"] != prose:
        fail("prose is no longer passed through as the answer")
    else:
        print("  ok  deliberate prose still becomes the answer")


def check_parser_repairs_a_stray_escape() -> None:
    """An escape JSON does not have must not put the envelope on screen.

    Seen 2026-11-01 (Bangkok weather): "**Rainfall**\\mThe" -- a newline
    written as \\m. Both parses failed and the user saw answer, digest and
    next_step as one block of text with every \\n in it.
    """
    from core.graph import _parse_decision

    reply = ('{"next": "finish", "answer": "\\n\\n**Rainfall**\\mThe storm. '
             '\\* not a list, C:\\\\x, \\"q\\"", "digest": "Weather fetched.", '
             '"next_step": {"label": "Get land cover"}}')
    d = _parse_decision(reply)
    want = '\n\n**Rainfall**\nThe storm. \\* not a list, C:\\x, "q"'
    if d.get("answer") != want:
        fail(f"a stray escape still breaks the parse: {d.get('answer')!r}")
    elif d.get("digest") != "Weather fetched." or not isinstance(d.get("next_step"), dict):
        fail(f"the digest or next_step was lost: {d}")
    else:
        print("  ok  \\m is read as a newline, \\* keeps its backslash, the rest parses")


def main() -> int:
    hr("1. the last turn is reserved for an answer")
    check_last_turn_reserved()
    hr("2. finishing early is not flagged")
    check_normal_stop_is_not_flagged()
    hr("3. a one-turn budget is not flagged")
    check_single_turn_budget()
    hr("4. a reply cut off at the token ceiling")
    check_truncation_is_marked()
    check_parser_refuses_a_truncated_decision()
    hr("5. an escape JSON does not have")
    check_parser_repairs_a_stray_escape()

    print()
    if FAILURES:
        print(f"{'=' * 68}\n{len(FAILURES)} CHECK(S) FAILED\n{'=' * 68}")
        for message in FAILURES:
            print(f"  - {message}")
        return 1
    print(f"{'=' * 68}\nALL CHECKS PASSED\n{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
