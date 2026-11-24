---
flow: accuracy_interpretation
card_note: Interpreting agreement with a reference flood map - flow card
title: Interpreting agreement with a reference flood map
nexus: other
select_when: The user asks how accurate, reliable or validated the NRT flood products are, how they agree with the UNOSAT or another reference map, or whether the metric order supports the TOPSIS ranking -- and accuracy_metrics has produced its table.
after_workflow: accuracy_metrics
consumes: [accuracy_metrics]
consumes_schema: ""
knowledge_note: Agreement metrics for binary flood maps
sources_note: Flood map validation - sources
workflow_order: 2
prev_node: monty
next_node: argos
status: draft
---

# Interpreting agreement with a reference flood map

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Entro/Interpreting agreement with a reference flood map - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

