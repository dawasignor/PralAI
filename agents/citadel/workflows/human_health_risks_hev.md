---
flow: human_health_risks_hev
card_note: Human health risks HEV - flow card
title: Human health risks HEV
nexus: water
select_when: The user asks for a health risk report for this flood, or the health risks of the event as a whole -- sewage and infection, contaminated drinking water, mosquitoes and malaria, mould -- or for Module 10. Writes the whole module; prefer it over the single-topic health flows for such a question.
inputs_required: [5, 8, 9, 12, 15, 16, 20, 22]
knowledge_note: Module 10 - Human health risks template
sources_note: Human health risk - sources
module: human_health_risks_hev
workflow_order: 1
prev_node: argos
next_node: argos
status: ready
---

# Human health risks HEV

## Flow Card

In the vault: section `## Flow Card` of `Heart/Flow cards/Chanakya/Human health risks HEV - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

`module: human_health_risks_hev` makes the specialist node write that module (`core/pipelines/hev.py`) instead of answering in free prose.
