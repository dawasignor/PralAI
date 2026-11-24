---
flow: essential_services
card_note: Essential services - flow card
title: Essential services
nexus: food
select_when: The user asks whether people can still reach a hospital, receive food aid or receive drinking water after a flood -- hospital access, road disruption, relief distribution, rations, supply routes. For a whole essential services report or Module 9, prefer essential_services_hev.
inputs_required: [3, 4, 5, 8, 9, 11, 12, 15, 16]
knowledge_note: Essential services
sources_note: Essential services - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: draft
---

# Essential services

## Flow Card

Moved to the vault on 2026-10-27 (renamed from Food aid and relief estimation on 2026-11-12): section `## Flow Card` of `Heart/Flow cards/Chanakya/Essential services - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Essential services` |
| `read_note` | `Essential services - sources` |

**The knowledge note is written** (hospital accessibility, food aid,
drinking-water aid). Until the sources note exists, the second call errors and
the rule in the system prompt applies: say so and stop.
