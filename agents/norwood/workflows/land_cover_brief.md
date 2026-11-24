---
flow: land_cover_brief
card_note: Land cover brief - flow card
title: Land cover brief
nexus: other
select_when: The Flood analyst pipeline has just fetched the pre-event land cover (Sentinel-2, or ESA WorldCover when Sentinel-2 failed its threshold) and asks for a brief on it -- the scene and its cloud, vegetation, the built-up index and the other indices. Terrain and water are not fetched yet at this point.
inputs_required: [3]
consumes: [lulc, worldcover]
workflow_order: 1
prev_node: argos
next_node: argos
status: ready
---

# Land cover brief

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Norwood/Land cover brief - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

