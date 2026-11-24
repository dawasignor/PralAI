"""Agent prompts and flow cards loaded from the vault (core/spec.py).

    pytest tests/test_prompt_notes.py
    python tests/test_prompt_notes.py           # fixture vault: no model, no network
    python tests/test_prompt_notes.py --live    # also the real vault at VAULT_ROOT

The prompts live in the vault; agents.md and the flow files keep the runtime settings and name the
note. Held still here, on a fixture vault built in a temporary folder:

* a persona note and a flow card load, with their hashes;
* a missing note, a note outside Heart/, a note of the wrong type, a note
  naming another agent or another flow, and an agents.md naming no note are
  all REFUSED with the note named -- never loaded, never degraded;
* the system prompt is assembled persona -> overlay -> carried memory, the
  most stable part first (prompt caching, R5);
* the hash changes when the text does.

With --live: every agent and every flow loads from the real vault, and no note
outside Heart/ claims to be a prompt.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import base_agent, spec, vault                        # noqa: E402

PERSONA = """---
type: agent-prompt
agent: {agent}
---

Notes for the user, not loaded.

## System Prompt

You are {agent}. SYSTEM-{agent}

## Resolver Prompt

RESOLVER-{agent}

## Notes

Not loaded either.
"""

CARD = """---
type: flow-card
agent: {agent}
flow: {flow}
---

## Flow Card

CARD-{flow}
"""


@contextmanager
def fixture(notes: dict[str, str], agents: dict[str, dict[str, str]]):
    """A vault holding `notes` (relative path -> text) and an agents folder
    holding `agents` (slug -> relative path -> text), swapped in for the call."""
    with tempfile.TemporaryDirectory() as tmp:
        root, agents_dir = Path(tmp) / "vault", Path(tmp) / "agents"
        for rel, text in notes.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        root.mkdir(exist_ok=True)
        for slug, files in agents.items():
            for rel, text in files.items():
                (agents_dir / slug / rel).parent.mkdir(parents=True, exist_ok=True)
                (agents_dir / slug / rel).write_text(text, encoding="utf-8")
        saved = os.environ.get("VAULT_ROOT"), spec.AGENTS_DIR
        os.environ["VAULT_ROOT"] = str(root)
        spec.AGENTS_DIR = agents_dir
        vault.shared_index(rebuild=True)
        try:
            yield root
        finally:
            if saved[0] is None:
                os.environ.pop("VAULT_ROOT", None)
            else:
                os.environ["VAULT_ROOT"] = saved[0]
            spec.AGENTS_DIR = saved[1]
            vault._SHARED_INDEX = None


def agents_md(note: str | None) -> str:
    line = f"prompt_note: {note}\n" if note is not None else ""
    return f"---\nname: Fake\nslug: fake\n{line}tools: []\nmax_turns: 2\n---\n\n# Fake\n"


def flow_md(card_note: str, flow: str = "a_flow") -> str:
    return (f"---\nflow: {flow}\ntitle: A flow\nselect_when: always\n"
            f"card_note: {card_note}\n---\n")


def refused(call, *words: str) -> None:
    try:
        call()
    except spec.PromptNoteError as exc:
        for word in words:
            assert word in str(exc), f"{word!r} not in: {exc}"
        return
    raise AssertionError("loaded a note that should have been refused")


GOOD_NOTES = {
    "Heart/Agent personas/Fake persona.md": PERSONA.format(agent="fake"),
    "Heart/Flow cards/A flow card.md": CARD.format(agent="fake", flow="a_flow"),
}


def test_persona_and_card_load() -> None:
    with fixture(GOOD_NOTES, {"fake": {"agents.md": agents_md("Fake persona"),
                                       "workflows/a_flow.md": flow_md("A flow card")}}):
        s = spec.load_spec("fake")
        assert s.system_prompt == "You are fake. SYSTEM-fake", s.system_prompt
        assert s.resolver_prompt == "RESOLVER-fake" and s.debug_prompt == ""
        assert set(s.prompt_shas) == {"system", "resolver"}, s.prompt_shas
        assert s.prompt_shas["system"] == spec.prompt_sha(s.system_prompt)
        flow = spec.load_flow("fake", "a_flow")
        assert flow.card == "CARD-a_flow" and flow.card_sha == spec.prompt_sha("CARD-a_flow")
        assert len(spec.check_all()) == 2


def test_missing_note_is_refused() -> None:
    with fixture({}, {"fake": {"agents.md": agents_md("Fake persona")}}):
        refused(lambda: spec.load_spec("fake"), "Fake persona", "cannot load")


def test_agents_md_naming_no_note_is_refused() -> None:
    with fixture(GOOD_NOTES, {"fake": {"agents.md": agents_md(None)}}):
        refused(lambda: spec.load_spec("fake"), "no agent-prompt note named")


def test_note_outside_heart_is_refused() -> None:
    notes = {"Head/Datasets/Fake persona.md": PERSONA.format(agent="fake")}
    with fixture(notes, {"fake": {"agents.md": agents_md("Fake persona")}}):
        refused(lambda: spec.load_spec("fake"), "Head", "only from Heart/")


def test_wrong_type_is_refused() -> None:
    notes = {"Heart/Fake persona.md": PERSONA.format(agent="fake").replace(
        "type: agent-prompt", "type: dataset")}
    with fixture(notes, {"fake": {"agents.md": agents_md("Fake persona")}}):
        refused(lambda: spec.load_spec("fake"), "'dataset'")


def test_note_for_another_agent_is_refused() -> None:
    notes = {"Heart/Fake persona.md": PERSONA.format(agent="norwood")}
    with fixture(notes, {"fake": {"agents.md": agents_md("Fake persona")}}):
        refused(lambda: spec.load_spec("fake"), "names agent 'norwood'")


def test_card_for_another_flow_is_refused() -> None:
    notes = dict(GOOD_NOTES)
    notes["Heart/Flow cards/A flow card.md"] = CARD.format(agent="fake", flow="other")
    with fixture(notes, {"fake": {"agents.md": agents_md("Fake persona"),
                                  "workflows/a_flow.md": flow_md("A flow card")}}):
        refused(lambda: spec.load_flow("fake", "a_flow"), "names flow 'other'")
        refused(spec.check_all, "A flow card")


def test_note_without_system_prompt_is_refused() -> None:
    notes = {"Heart/Fake persona.md": "---\ntype: agent-prompt\nagent: fake\n---\n\nnothing\n"}
    with fixture(notes, {"fake": {"agents.md": agents_md("Fake persona")}}):
        refused(lambda: spec.load_spec("fake"), "System Prompt")


def test_assembly_order_is_stable_first() -> None:
    fake = types.SimpleNamespace(
        slug="fake", spec=types.SimpleNamespace(system_prompt="PERSONA"))
    saved = base_agent.memory.read_active_state
    base_agent.memory.read_active_state = lambda slug: "MEMORY"
    try:
        text = base_agent.BaseAgent.system_prompt(fake, "OVERLAY")
    finally:
        base_agent.memory.read_active_state = saved
    order = [text.index(part) for part in ("PERSONA", "OVERLAY", "MEMORY")]
    assert order == sorted(order), text


def test_hash_follows_the_text() -> None:
    assert spec.prompt_sha("a") == spec.prompt_sha("a")
    assert spec.prompt_sha("a") != spec.prompt_sha("a ")
    assert len(spec.prompt_sha("a")) == 12


def live_every_prompt_loads() -> None:
    lines = spec.check_all()
    slugs = spec.agent_slugs()
    assert {line.split(":")[0] for line in lines} >= set(slugs), lines
    for slug in slugs:
        s = spec.load_spec(slug)
        assert s.system_prompt and s.prompt_note, slug


def live_no_prompt_outside_heart() -> None:
    index = vault.shared_index(rebuild=True)
    stray = [title for title, meta in index.meta.items()
             if meta.get("type") in (spec.AGENT_PROMPT, spec.FLOW_CARD)
             and index.by_title[title].relative_to(index.root).parts[0] != spec.PROMPT_FOLDER]
    assert not stray, f"prompt-type notes outside Heart/: {stray}"


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    if "--live" in sys.argv:
        tests += [live_every_prompt_loads, live_no_prompt_outside_heart]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
