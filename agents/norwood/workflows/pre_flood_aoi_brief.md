---
flow: pre_flood_aoi_brief
card_note: Pre-flood AOI brief - flow card
title: Pre-flood AOI brief
nexus: other
select_when: The user asks what an AOI looked like before a flood, for a pre-event land-cover or terrain characterisation, or for a briefing on how a flood map over this area is likely to behave. Requires Monty's lulc, permanent_water and terrain payloads to already exist.
inputs_required: [2, 3, 4, 5]
consumes: [lulc, terrain, permanent_water, weather_grid]
consumes_schema: norwood/pre-flood-aoi-brief
knowledge_note: Norwood pre-flood AOI brief schema
sources_note: Norwood pre-flood AOI brief schema
schema_file: docs/norwood_preflood_brief.schema.json
workflow_order: 1
prev_node: argos
next_node: argos
status: draft
---

# Pre-flood AOI brief

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Norwood/Pre-flood AOI brief - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

