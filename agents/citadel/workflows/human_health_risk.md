---
flow: human_health_risk
card_note: Human health risk - flow card
title: Human health risk
nexus: water
select_when: The user asks about a single human health risk after a flood -- sewage, wastewater, sanitation failure, contaminated floodwater, infection or disease risk, injuries, mental health, or mould in flooded buildings. For a whole health risk report or Module 10, prefer human_health_risks_hev.
inputs_required: [1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 20]
knowledge_note: Human health risk
sources_note: Human health risk - sources
prominent_sources: [WHO 2005, WHO 2006, Yang et al. 2024, Ehalt Macedo et al. 2022, WHO 2024]
workflow_order: 1
prev_node: argos
next_node: argos
status: draft
---

# Human health risk

## Flow Card

Moved to the vault on 2026-10-27 (renamed from Sewage and infection risk on 2026-11-12): section `## Flow Card` of `Heart/Flow cards/Chanakya/Human health risk - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

Both are vault notes, fetched from the MCP server at the top of the run:

| Call | `ref` | Holds |
|---|---|---|
| `read_note` | `Human health risk` | The evidence, cited short as `(WHO 2024)` |
| `read_note` | `Human health risk - sources` | The full reference behind every key |

This card holds **no figures and no references**, so it cannot drift from
either note. The vault is where a figure and its citation live together; the
sources note is what gets shipped to the reader.

`docs/SOURCES.md` section 7 remains the repo's audit ledger -- which sources
this project relies on, their vintage and how far each was verified. It is for
a human checking the work and is never retrieved at run time.

Both calls are live. `read_note` reads the vault through `core/vault.py`, the
same code `mcp_servers/vault_server.py` serves over stdio, so an answer given
in the graph and one given to an external MCP client come from identical bytes.

`search_notes` is available too, for a follow-up question this card does not
cover -- search first, then `read_note` what it returns.

**Still missing:** Argos does not select a flow yet, so this card is not yet
overlaid automatically in a real run.
