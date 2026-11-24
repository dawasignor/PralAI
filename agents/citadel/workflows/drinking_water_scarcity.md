---
flow: drinking_water_scarcity
card_note: Drinking water scarcity - flow card
title: Drinking water scarcity
nexus: water
select_when: The user asks about drinking water, safe or potable supply, taps running dry, boil advisories, water trucking, or how long it takes to restore water after a flood.
inputs_required: [1, 4, 5, 8, 9, 15, 16, 20]
knowledge_note: Drinking water scarcity
sources_note: Drinking water scarcity - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Drinking water scarcity

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Drinking water scarcity - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Drinking water scarcity` |
| `read_note` | `Drinking water scarcity - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
