---
flow: rockfall_treefall_and_landslide_risk
card_note: Rockfall, treefall and landslide risk - flow card
title: Rockfall, treefall and landslide risk
nexus: other
select_when: The user asks about landslides, slope failure, rockfall, mudslides, collapsing hillsides or falling trees during or after heavy rainfall.
inputs_required: [1, 2, 3, 5, 8, 9, 17]
knowledge_note: Rockfall treefall and landslide risk
sources_note: Rockfall treefall and landslide risk - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Rockfall, treefall and landslide risk

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Rockfall, treefall and landslide risk - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Rockfall treefall and landslide risk` |
| `read_note` | `Rockfall treefall and landslide risk - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
