---
flow: cultural_heritage_and_home_flooding_risk
card_note: Cultural heritage and home flooding risk - flow card
title: Cultural heritage and home flooding risk
nexus: other
select_when: The user asks about heritage buildings, monuments, temples, museums or irreplaceable structures, or about water entering homes and displacing residents.
inputs_required: [3, 4, 5, 9, 10, 18]
knowledge_note: Cultural heritage and home flooding risk
sources_note: Cultural heritage and home flooding risk - sources
workflow_order: 1
prev_node: argos
next_node: argos
status: stub
---

# Cultural heritage and home flooding risk

## Flow Card

Moved to the vault on 2026-10-27: section `## Flow Card` of `Heart/Flow cards/Chanakya/Cultural heritage and home flooding risk - flow card.md`. Edit it there; core/spec.py loads it through the `card_note` line in the frontmatter above.

## Knowledge and sources

| Call | `ref` |
|---|---|
| `read_note` | `Cultural heritage and home flooding risk` |
| `read_note` | `Cultural heritage and home flooding risk - sources` |

**Neither note carries evidence yet.** The knowledge note is a scaffold and the
sources note does not exist, so the second call errors and the rule in the
system prompt applies: say so and stop. That is the correct outcome -- an
analysis with no sourced figures behind it is worth less than an honest
refusal. Writing the evidence is what makes this flow live; nothing in this
card changes when it does.
