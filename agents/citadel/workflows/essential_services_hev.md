---
flow: essential_services_hev
card_note: Essential services HEV - flow card
title: Essential services HEV
nexus: infrastructure
select_when: The user asks about the risks to essential services in this flood -- hospitals, drinking water, food shops and markets -- as a whole, or for Module 9. Writes the whole module (map, people, vulnerability, risks and actions); prefer it over the single-topic flows for such a question.
inputs_required: [5, 8, 9, 12, 15, 16, 20, 22]
knowledge_note: Module 9 - Essential services template
sources_note: Human health risk - sources
module: essential_services_hev
workflow_order: 1
prev_node: argos
next_node: argos
status: ready
---

# Essential services HEV

## Flow Card

In the vault: section `## Flow Card` of `Heart/Flow cards/Chanakya/Essential services HEV - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

`module: essential_services_hev` makes the specialist node write that module (`core/pipelines/hev.py`) instead of answering in free prose.
