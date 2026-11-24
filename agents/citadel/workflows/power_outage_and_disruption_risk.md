---
flow: power_outage_and_disruption_risk
card_note: Power outage and disruption risk - flow card
title: Power outage and disruption risk
nexus: energy
select_when: The user asks about electricity supply, power cuts, substations, grid damage, restoration time, or what else stops working when the power goes.
inputs_required: [1, 3, 4, 5, 9, 13, 16]
knowledge_note: Power outage and disruption risk
sources_note: Power outage and disruption risk - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Power outage and disruption risk

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Power outage and disruption risk - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Power outage and disruption risk` |
| `read_note` | `Power outage and disruption risk - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
