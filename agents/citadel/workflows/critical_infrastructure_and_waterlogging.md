---
flow: critical_infrastructure_and_waterlogging
card_note: Critical infrastructure and waterlogging - flow card
title: Critical infrastructure and waterlogging
nexus: water
select_when: The user asks about hospitals, clinics, emergency services, ambulance or evacuation routes, roads cut by water, or which facilities stay reachable during a flood.
inputs_required: [3, 4, 5, 9, 11, 12, 16]
knowledge_note: Critical infrastructure and waterlogging
sources_note: Critical infrastructure and waterlogging - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Critical infrastructure and waterlogging

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Critical infrastructure and waterlogging - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Critical infrastructure and waterlogging` |
| `read_note` | `Critical infrastructure and waterlogging - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
