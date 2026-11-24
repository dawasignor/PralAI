---
flow: event_commentary
card_note: Event commentary - flow card
title: Event commentary
nexus: other
select_when: The Python agent has just fetched the event weather, the land cover (Sentinel-2 or WorldCover), the terrain or the JRC water bodies, and the result needs the remote sensing expert's comments on what it and its figure mean for this flood event. Claimed automatically after those fetches; not asked for by a user.
inputs_required: [1, 2, 3, 4, 5]
after_workflow: [fetch_weather, fetch_lulc_data, fetch_worldcover, fetch_terrain_data, fetch_permanent_water]
consumes: [weather_grid, lulc, worldcover, terrain, permanent_water]
consumes_schema: norwood/event-commentary
append_to_answer: true
workflow_order: 1
prev_node: argos
next_node: argos
status: ready
---

# Event commentary

## Flow Card

In the vault: section `## Flow Card` of `Heart/Flow cards/Norwood/Event commentary - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

`after_workflow` lists every fetch this flow comments on: when any of them
succeeds in a turn, Argus routes here before answering (one hop, as Entro's
accuracy interpretation). `append_to_answer` puts the reply under Argus'
answer as written, under "What this means for this flood event"; a reply of
"No comment." adds nothing.
