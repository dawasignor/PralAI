---
flow: flood_map_handoff
card_note: Flood map handoff - flow card
title: Flood map handoff
nexus: other
select_when: Module 9 or 10 is being written and the stored flood map must be handed over to the city planner -- run by the module's own steps once the map is drawn, never asked for by a user.
inputs_required: [3, 4]
module: ''
workflow_order: 1
prev_node: argos
next_node: argos
status: ready
---

# Flood map handoff

## Flow Card

In the vault: section `## Flow Card` of `Heart/Flow cards/Norwood/Flood map handoff - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

Not asked for by a user: run by `core/pipelines/hev.py` while Module 9 or 10 is written.
