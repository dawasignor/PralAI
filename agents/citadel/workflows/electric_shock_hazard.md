---
flow: electric_shock_hazard
card_note: Electric shock hazard - flow card
title: Electric shock hazard
nexus: energy
select_when: The user asks about electrocution, live or downed power lines in water, whether it is safe to wade or re-enter a flooded building, or electrical safety during a flood.
inputs_required: [3, 4, 5, 9, 13, 16, 20]
knowledge_note: Electric shock hazard
sources_note: Electric shock hazard - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Electric shock hazard

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Electric shock hazard - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Electric shock hazard` |
| `read_note` | `Electric shock hazard - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
