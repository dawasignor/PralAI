"""Flow cards: loading, the routing menu, and the name check Argos is held to.

    python tests/test_flows.py

Offline and dependency-light on purpose -- it imports `core.spec` only, never
`core.graph`, so it runs without langgraph installed. The selection logic lives
in spec.py for exactly that reason: the graph reads `state["flow"]` and never
decides it, so everything worth testing is here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import spec                                       # noqa: E402

FAILURES: list[str] = []
CARD_TOKEN_BUDGET = 1_200          # chars/4; past this, evidence has leaked in


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def check_cards() -> None:
    """Every card loads, and declares what the loader and Argos need."""
    flows = spec.list_flows("citadel")
    if not flows:
        fail("citadel has no flows -- nothing to select")
        return

    for flow in flows:
        tokens = len(flow.card) // 4
        print(f"  {flow.name:<28} ~{tokens:>4} tokens  "
              f"inputs={flow.inputs_required}")

        if not flow.select_when:
            fail(f"{flow.name}: no select_when -- Argos cannot route to it")
        if not flow.knowledge_note:
            fail(f"{flow.name}: no knowledge_note -- nothing to retrieve")
        if not flow.inputs_required:
            fail(f"{flow.name}: declares no inputs")

        # A card is procedure. Numbers belong in the knowledge note, where they
        # sit beside their citation and cannot drift from it.
        if tokens > CARD_TOKEN_BUDGET:
            fail(f"{flow.name}: card is ~{tokens} tokens, over the "
                 f"{CARD_TOKEN_BUDGET} budget -- evidence has leaked in")


def check_menu() -> None:
    """The routing menu: one line per flow, trigger only."""
    menu = spec.flow_menu("citadel")
    print(f"  menu:\n    " + menu.replace("\n", "\n    "))

    flows = spec.list_flows("citadel")
    if len(menu.splitlines()) != len(flows):
        fail(f"menu has {len(menu.splitlines())} lines for {len(flows)} flows")

    for flow in flows:
        if flow.name not in menu:
            fail(f"{flow.name} missing from the menu")
        # The menu must stay a menu. A card in it would defeat the split.
        if flow.card[:80] in menu:
            fail(f"{flow.name}: card text leaked into the routing menu")

    # Flows are not Citadel's alone. Norwood gained `pre_flood_aoi_brief` on
    # 2026-09-22, so the check is that a menu holds exactly that agent's own
    # cards -- which is the property that matters. `entro` has none and is the
    # empty case now.
    norwood = {f.name for f in spec.list_flows("norwood")}
    menu_n = spec.flow_menu("norwood")
    if not norwood:
        fail("norwood should carry pre_flood_aoi_brief")
    elif any(name not in menu_n for name in norwood):
        fail(f"a norwood card is missing from norwood's menu: {norwood}")
    elif any(f.name in menu_n for f in flows):
        fail("a citadel flow leaked into norwood's menu")
    else:
        print(f"  norwood's menu holds only its own {len(norwood)} card(s)")

    # Monty runs workflows, not flows. Entro was the example here until it got
    # its first card (accuracy_interpretation, 2026-10-11).
    if spec.flow_menu("monty") != "":
        fail("an agent with no flows should contribute nothing to the menu")
    else:
        print("  an agent with no flows contributes nothing")
    if "accuracy_interpretation" not in spec.flow_menu("entro"):
        fail("entro's menu should offer accuracy_interpretation")
    else:
        print("  entro's menu offers accuracy_interpretation")

    if spec.flow_menu("no_such_agent") != "":
        fail("an unknown agent should yield an empty menu, not raise")


def check_validation() -> None:
    """What Argos is allowed to choose, and what happens when it invents one."""
    cases = [
        ("citadel", "human_health_risk", "human_health_risk",
         "a real flow is accepted"),
        ("citadel", "", "", "no flow means the general path"),
        ("citadel", "  ", "", "whitespace is not a flow"),
        ("citadel", "sewage", "", "a near-miss is rejected, not fuzzy-matched"),
        ("citadel", "invented_flow", "", "an invented name is rejected"),
        ("norwood", "human_health_risk", "",
         "another agent's flow is not borrowable"),
        ("no_such_agent", "human_health_risk", "",
         "an unknown agent yields nothing"),
    ]
    for slug, given, expected, why in cases:
        got = spec.valid_flow(slug, given)
        mark = "ok " if got == expected else "FAIL"
        print(f"  [{mark}] {why}: valid_flow({slug!r}, {given!r}) -> {got!r}")
        if got != expected:
            fail(f"{why}: expected {expected!r}, got {got!r}")


def check_card_and_note_agree() -> None:
    """The card names notes; the vault must actually hold them.

    Skipped when VAULT_ROOT is not set -- the card is still valid without a
    vault, it just cannot retrieve.
    """
    from core import vault

    try:
        index = vault.build_index()
    except vault.VaultError as exc:
        print(f"  SKIPPED: {exc}")
        return

    for flow in spec.list_flows("citadel"):
        for field in ("knowledge_note",):
            ref = getattr(flow, field)
            try:
                note = vault.read_note(index, ref)
            except vault.VaultError as exc:
                fail(f"{flow.name}: {field} {ref!r} does not resolve: {exc}")
                continue

            print(f"  {flow.name}: {field} -> {note['path']}")

            # A stub declares that its evidence is unwritten, and the card says
            # so. Failing on that keeps the suite permanently red over a known
            # gap, which teaches a reader to ignore it -- so the gap is
            # reported and the check is held for the flows that claim to be
            # ready. A flow that stops claiming stub must bring its sources
            # note with it, and that is what this still catches.
            if flow.status == "stub":
                print(f"  {flow.name}: STUB -- no sources note expected yet")
                continue

            # A card that names no sources note does not retrieve one. The
            # general-question path answers from the session and fetching
            # anything would turn a question into an analysis nobody asked for.
            if not flow.sources_note:
                print(f"  {flow.name}: retrieves nothing -- no sources note")
                continue

            sources_ref = note["frontmatter"].get("sources_note")
            if not sources_ref:
                fail(f"{flow.name}: knowledge note names no sources_note")
                continue
            try:
                vault.read_note(index, sources_ref)
                print(f"  {flow.name}: sources_note -> {sources_ref}")
            except vault.VaultError as exc:
                fail(f"{flow.name}: sources_note {sources_ref!r} missing: {exc}")


def main() -> int:
    hr("1. cards load and declare what routing needs")
    check_cards()
    hr("2. the routing menu Argos sees")
    check_menu()
    hr("3. the name check Argos is held to")
    check_validation()
    hr("4. cards point at notes that exist")
    check_card_and_note_agree()

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
