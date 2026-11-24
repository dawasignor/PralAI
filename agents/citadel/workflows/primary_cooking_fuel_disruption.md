---
flow: primary_cooking_fuel_disruption
card_note: Primary cooking fuel disruption - flow card
title: Primary cooking fuel disruption
nexus: food
select_when: The user asks about cooking, stoves, LPG or gas cylinders, piped gas, firewood, or whether households can prepare food and boil water after a flood.
inputs_required: [5, 8, 9, 13, 14, 16]
knowledge_note: Primary cooking fuel disruption
sources_note: Primary cooking fuel disruption - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Primary cooking fuel disruption

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Primary cooking fuel disruption - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Primary cooking fuel disruption` |
| `read_note` | `Primary cooking fuel disruption - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
