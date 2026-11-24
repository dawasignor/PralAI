"""Vault reader checks. Offline -- builds its own fixture vault.

    python tests/test_vault.py            # fixture only
    python tests/test_vault.py --live     # also index the real VAULT_ROOT

The guards are tested, not asserted: a path that escapes the vault, a title two
notes share, a citation with no reference behind it. Those are the three ways
this reader could quietly return the wrong thing.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import vault                                      # noqa: E402
from mcp_servers import vault_server                        # noqa: E402

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
KNOWLEDGE = """---
type: workflow
desc: Fixture knowledge note
aliases: [fixture knowledge, fx knowledge]
keywords: [sewage, chlorination, fixture]
retrieval: Answer what the fixture knowledge note is for.
sources_note: Fixture sources
status: draft
tags: [fixture, workflow]
---

A figure that is cited (WHO 2024) and another one (UNEP 2023).
A key with no reference behind it (GHOST 1999).
Not a citation (see the table above) and not this one (it depends).
"""

SOURCES = """---
type: sources
desc: Fixture sources note
aliases: [fixture refs]
keywords: [sources, fixture]
retrieval: Return full references for the fixture keys.
status: draft
tags: [fixture, sources]
---

### (WHO 2024)

World Health Organization. Sanitation fact sheet.

### (UNEP 2023)

United Nations Environment Programme. Wastewater.

### (NEVER CITED)

A reference nothing cites.
"""

PLAIN = """---
type: note
desc: A note with no sources_note
keywords: [terrain, slope]
retrieval: Answer what a plain note looks like.
status: stub
tags: [fixture]
---

No citations here at all.
"""


def make_vault(root: Path) -> None:
    (root / "Head").mkdir(parents=True, exist_ok=True)
    (root / "Hand" / "Deep").mkdir(parents=True, exist_ok=True)
    (root / "Head" / "Fixture knowledge.md").write_text(KNOWLEDGE, encoding="utf-8")
    (root / "Head" / "Fixture sources.md").write_text(SOURCES, encoding="utf-8")
    (root / "Hand" / "Plain note.md").write_text(PLAIN, encoding="utf-8")
    # Same title in two folders -- the ambiguity that must never be guessed.
    (root / "Head" / "Twin.md").write_text("---\ndesc: one\n---\nfirst\n", encoding="utf-8")
    (root / "Hand" / "Deep" / "Twin.md").write_text("---\ndesc: two\n---\nsecond\n", encoding="utf-8")
    # Outside the vault, for the confinement check.
    (root.parent / "outside.md").write_text("secret\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_index(index: vault.VaultIndex) -> None:
    if len(index.by_title) != 4:
        fail(f"expected 4 unique titles, got {len(index.by_title)}")
    if "Twin" not in index.duplicate_titles:
        fail("duplicate title 'Twin' was not recorded")
    else:
        print(f"  duplicate recorded: Twin -> {index.duplicate_titles['Twin']}")
    if index.by_alias.get("fx knowledge") is None:
        fail("alias 'fx knowledge' did not resolve")
    else:
        print("  alias resolves: 'fx knowledge' -> Fixture knowledge")


def check_resolution(index: vault.VaultIndex) -> None:
    note = vault.read_note(index, "Fixture knowledge")
    if note["title"] != "Fixture knowledge":
        fail(f"title round trip: {note['title']}")
    if "(WHO 2024)" not in note["content"]:
        fail("content is not verbatim -- citation missing")
    print("  read by title returns verbatim content")

    by_alias = vault.read_note(index, "fixture knowledge")
    if by_alias["path"] != note["path"]:
        fail("alias and title resolved to different notes")
    print("  read by alias resolves to the same note")

    by_link = vault.read_note(index, "[[Fixture knowledge]]")
    if by_link["path"] != note["path"]:
        fail("wikilink brackets were not stripped")
    print("  read by [[wikilink]] resolves")

    try:
        vault.read_note(index, "Twin")
        fail("ambiguous title was resolved instead of refused")
    except vault.VaultError as exc:
        if "ambiguous" not in str(exc):
            fail(f"ambiguous title raised the wrong error: {exc}")
        else:
            print("  ambiguous title refused rather than guessed")


def check_confinement(index: vault.VaultIndex) -> None:
    for escape in ("../outside", "../outside.md", "..\\outside.md"):
        try:
            vault.read_note(index, escape)
            fail(f"read escaped the vault via {escape!r}")
        except vault.VaultError as exc:
            if "outside the vault" not in str(exc) and "no note" not in str(exc):
                fail(f"{escape!r} raised the wrong error: {exc}")
    print("  paths escaping the vault are refused")


def check_citations(index: vault.VaultIndex) -> None:
    note = vault.read_note(index, "Fixture knowledge")
    report = note["citations"]

    if not report["checked"]:
        fail(f"citations were not checked: {report}")
    if report["resolved"]:
        fail("a note citing (GHOST 1999) was reported as fully resolved")
    if report.get("unresolved") != ["GHOST 1999"]:
        fail(f"unresolved keys wrong: {report.get('unresolved')}")
    if "NEVER CITED" not in (report.get("uncited_references") or []):
        fail("an uncited reference was not reported")
    for noise in ("It depends", "See the table above"):
        if noise in report["keys_used"]:
            fail(f"prose was mistaken for a citation: {noise!r}")
    print(f"  unresolved flagged: {report['unresolved']}")
    print(f"  uncited references: {report['uncited_references']}")
    print("  ordinary parentheses are not read as citations")

    # Fail-open: the note is still returned, in full.
    if "(WHO 2024)" not in note["content"]:
        fail("fail-open broken -- content withheld when a citation was unresolved")
    print("  fail-open: content served despite the unresolved citation")

    plain = vault.read_note(index, "Plain note")["citations"]
    if plain["checked"] or not plain["resolved"]:
        fail(f"a note with no sources_note should pass unchecked: {plain}")
    print("  a note naming no sources_note is not penalised")


def check_truncation(index: vault.VaultIndex) -> None:
    note = vault.read_note(index, "Fixture knowledge", max_chars=40)
    if not note["truncated"]:
        fail("truncation was not flagged")
    if len(note["content"]) != 40:
        fail(f"truncated to {len(note['content'])} chars, expected 40")
    if note["chars"] <= 40:
        fail("original length was not reported alongside the truncation")
    print(f"  truncation flagged, full length reported ({note['chars']} chars)")


def check_search(index: vault.VaultIndex) -> None:
    results = vault.search_notes(index, "chlorination")
    if not results or results[0]["title"] != "Fixture knowledge":
        fail(f"keyword search did not rank the right note first: {results}")
    else:
        print(f"  keyword hit ranks first (score {results[0]['score']})")

    if [r for r in vault.search_notes(index, "slope") if r["title"] == "Plain note"]:
        print("  second note found by its own keyword")
    else:
        fail("keyword search missed 'slope'")

    if vault.search_notes(index, "zzzznotpresent"):
        fail("search invented a match for a nonsense query")
    else:
        print("  nonsense query returns nothing")


def check_listing(index: vault.VaultIndex) -> None:
    if len(vault.list_notes(index)) != 4:
        fail("list_notes did not return every note")
    head = vault.list_notes(index, folder="Head")
    if not head or any(not r["path"].startswith("Head") for r in head):
        fail(f"folder filter leaked: {head}")
    print(f"  folder filter returns {len(head)} notes under Head/")
    if len(vault.list_notes(index, note_type="sources")) != 1:
        fail("type filter did not isolate the sources note")
    print("  type filter isolates the sources note")


def check_dispatch(index: vault.VaultIndex) -> None:
    """The MCP layer must return errors as values, not raise into the transport."""
    out = vault_server.dispatch(index, "read_note", {"ref": "Fixture knowledge"})
    if out.get("title") != "Fixture knowledge":
        fail(f"dispatch read_note failed: {out}")

    bad = vault_server.dispatch(index, "read_note", {"ref": "Twin"})
    if "error" not in bad:
        fail("dispatch did not turn an ambiguous title into an error value")
    else:
        print("  ambiguous title -> error value, not an exception")

    missing = vault_server.dispatch(index, "read_note", {})
    if "error" not in missing:
        fail("dispatch did not report a missing argument")

    unknown = vault_server.dispatch(index, "no_such_tool", {})
    if "error" not in unknown:
        fail("dispatch did not report an unknown tool")
    print("  missing argument and unknown tool both reported")

    notice = out.get("notice", "")
    if "DATA, not instructions" not in notice:
        fail("the data-not-instructions notice is missing from a read")
    print("  every read carries the data-not-instructions notice")


def check_live() -> None:
    index = vault.build_index()
    print(f"  vault: {index.root}")
    print(f"  notes: {len(index.by_title)}, commit: {index.commit}")
    for title, paths in index.duplicate_titles.items():
        fail(f"live vault has a duplicate title {title!r}: {paths}")

    note = vault.read_note(index, "Human health risk")
    report = note["citations"]
    print(f"  health risk note: {note['chars']} chars, "
          f"citations checked={report['checked']} resolved={report['resolved']}")
    if report["checked"] and not report["resolved"]:
        fail(f"live health risk note has unresolved citations: {report['unresolved']}")
    if note["truncated"]:
        fail("live health risk note was truncated at the default cap")


def check_live_retrieval() -> None:
    """What can a reader learn about this vault, knowing nothing about it?

    This is the question an agent actually arrives with. It should be able to
    orient itself -- what is in here, what answers a topic -- without being told
    a single filename in advance.
    """
    index = vault.build_index()

    print("  what is in the vault, by top-level folder:")
    folders: dict[str, int] = {}
    for row in vault.list_notes(index, limit=1000):
        folders[row["path"].split("/")[0]] = folders.get(
            row["path"].split("/")[0], 0) + 1
    for folder, count in sorted(folders.items(), key=lambda kv: -kv[1]):
        print(f"    {folder:<12} {count:>3} notes")
    if len(folders) < 2:
        fail(f"expected several top-level folders, saw {list(folders)}")

    print("\n  maps of content, which is where an agent starts:")
    mocs = [r for r in vault.list_notes(index, limit=1000)
            if r["title"].endswith("MOC") or "MOC" in r["title"]]
    for row in mocs[:6]:
        print(f"    {row['title']}")
    if not mocs:
        fail("no MOC notes found -- an agent has no entry point")

    # How much of the vault can be found by topic at all. A note with no
    # `retrieval:` line is ranked on its title and body alone, and one with no
    # frontmatter is close to invisible -- it cannot be found by a question,
    # only by already knowing its name. This is vault hygiene, not a code
    # fault, so it is reported rather than failed.
    bare = [t for t in sorted(index.by_title) if not index.meta.get(t)]
    thin = [t for t in sorted(index.by_title)
            if index.meta.get(t) and not index.meta[t].get("retrieval")]
    print(f"\n  searchability: {len(index.by_title) - len(bare) - len(thin)}"
          f"/{len(index.by_title)} notes carry a retrieval line")
    if bare:
        print(f"    {len(bare)} with no frontmatter at all -- findable only by "
              f"name: {bare[:4]}{' ...' if len(bare) > 4 else ''}")
    if thin:
        print(f"    {len(thin)} with frontmatter but no retrieval line: "
              f"{thin[:4]}{' ...' if len(thin) > 4 else ''}")

    # Topic -> the note that should answer it. Every note named here declares a
    # retrieval line, so a miss IS a retrieval failure rather than a note that
    # never advertised itself.
    print("\n  topic retrieval:")
    expected = {
        "sewage and infection risk after a flood": "Human health risk",
        "human health risk after a flood": "Human health risk",
        "full references for the sewage analysis": "Human health risk - sources",
        "how reliable is the built-up fraction": "Built-up fraction as an MCDM criterion",
        "what does a node in a workflow mean": "Chanakya node schema",
    }
    for query, wanted in expected.items():
        if not index.meta.get(wanted, {}).get("retrieval"):
            fail(f"test fixture is wrong: {wanted!r} declares no retrieval line")
            continue
        hits = vault.search_notes(index, query, limit=5)
        titles = [h["title"] for h in hits]
        mark = "ok " if wanted in titles else "MISS"
        rank = titles.index(wanted) + 1 if wanted in titles else None
        print(f"    [{mark}] {query!r}")
        print(f"           -> {titles[:3]}")
        if wanted not in titles:
            fail(f"retrieval missed {wanted!r} for {query!r}: got {titles}")
        elif rank and rank > 3:
            fail(f"{wanted!r} ranked {rank} for {query!r} -- too deep to be useful")

    # Every hit carries the line that says what question it answers, which is
    # what lets an agent choose between two plausible titles.
    top = vault.search_notes(index, "uncertainty in the built-up fraction", limit=1)
    if top and not top[0].get("retrieval"):
        fail(f"search result carries no retrieval line: {top[0]['title']}")
    elif top:
        print(f"\n  a hit explains itself:")
        print(f"    {top[0]['title']}")
        print(f"    retrieval: {str(top[0]['retrieval'])[:96]}...")


def check_flow_retrieval_through_tools() -> None:
    """The exact two calls the sewage card tells Citadel to make.

    Goes through core.tools.execute, not core.vault, because that is the path
    the agent takes -- registry lookup, JSON in, JSON string out.
    """
    import json

    from core import spec, tools

    flow = spec.load_flow("citadel", "human_health_risk")
    print(f"  card: {flow.title} ({len(flow.card)} chars of procedure)")

    knowledge = json.loads(tools.execute("read_note", {"ref": flow.knowledge_note}))
    if "error" in knowledge:
        fail(f"knowledge retrieval failed: {knowledge['error']}")
        return
    if knowledge.get("stub"):
        fail("read_note returned stub output -- the tool is still a placeholder")

    sources_ref = knowledge["frontmatter"].get("sources_note")
    if not sources_ref:
        fail("knowledge note names no sources_note")
        return
    sources = json.loads(tools.execute("read_note", {"ref": sources_ref}))
    if "error" in sources:
        fail(f"sources retrieval failed: {sources['error']}")
        return

    print(f"  call 1 read_note({flow.knowledge_note!r}) -> "
          f"{knowledge['chars']} chars, truncated={knowledge['truncated']}")
    print(f"  call 2 read_note({sources_ref!r}) -> "
          f"{sources['chars']} chars, truncated={sources['truncated']}")

    report = knowledge["citations"]
    print(f"  citations: resolved={report['resolved']} "
          f"keys={len(report['keys_used'])} unresolved={report.get('unresolved')}")
    if not report["resolved"]:
        fail(f"live health risk note has unresolved citations: {report['unresolved']}")

    # The card's prominent_sources are what the answer closes with, so each one
    # must actually expand in the sources note that was just fetched.
    prominent = knowledge["frontmatter"].get("prominent_sources") or []
    declared = vault.citation_keys_declared(
        vault.split_frontmatter(sources["content"])[1])
    for key in prominent:
        if key not in declared:
            fail(f"prominent source ({key}) has no reference in the sources note")
    print(f"  prominent sources all expand: {prominent}")

    # search_notes must be real too -- a flow may follow up with it.
    found = json.loads(tools.execute("search_notes", {"query": "leptospirosis"}))
    titles = [r["title"] for r in found.get("results", [])]
    if flow.knowledge_note not in titles:
        fail(f"search_notes did not find the note by a term inside it: {titles}")
    else:
        print(f"  search_notes('leptospirosis') -> {titles[:2]}")


def main() -> int:
    live = "--live" in sys.argv

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "vault"
        root.mkdir()
        make_vault(root)
        index = vault.build_index(root)

        hr("1. index")
        check_index(index)
        hr("2. resolution")
        check_resolution(index)
        hr("3. path confinement")
        check_confinement(index)
        hr("4. citations -- fail open, flag loudly")
        check_citations(index)
        hr("5. truncation")
        check_truncation(index)
        hr("6. search")
        check_search(index)
        hr("7. listing")
        check_listing(index)
        hr("8. MCP dispatch")
        check_dispatch(index)

    if live:
        hr("9. live: the real vault")
        check_live()
        hr("10. live: what is this vault about?")
        check_live_retrieval()
        hr("11. live: the flow's two calls, through the tool registry")
        check_flow_retrieval_through_tools()
    else:
        hr("SKIPPED: live vault (pass --live with VAULT_ROOT set)")

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
