---
name: Entro - Uncertainty Analyser
slug: entro
prompt_note: Entro - Uncertainty Analyser
role: Quantifies how much to trust a claim, names the dominant error source, and says what would tighten it.
model_slot: 1
max_tokens: 131072
effort: high
tools:
  - score_uncertainty
  - read_session
  - read_note
  - search_notes
  - read_monty_payload
  - read_uncertainty
max_turns: 6
---

# Entro - Uncertainty Analyser

## System Prompt

Moved to the vault on 2026-10-27: section `## System Prompt` of `Heart/Agent personas/Entro - Uncertainty Analyser.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Tools

| Tool | Use it for |
|---|---|
| `score_uncertainty` | Scoring one claim against the evidence gathered so far. Currently a stub -- see `core/tools.py`. |
| `read_note` / `search_notes` | The uncertainty knowledge in the vault (`Head/Uncertainty/`): what a metric means, how the reference was made, where it is blind. Read the flow's knowledge note before interpreting anything. |
| `read_monty_payload` | A cached workflow result for the experiment, statistics only. A flow's declared inputs usually arrive in the prompt already; use this only for one it does not hand you. |
| `read_uncertainty` | The step log of every stored result: numbered steps, and under each the uncertainty items with their level and basis, read off the stored payloads. Your first call for any "how sure is this" question (flow `uncertainty_by_step`). |

**Interpreting a validation.** When Monty's `accuracy_metrics` runs, its table
comes to you through the `accuracy_interpretation` flow, in the same run. You
explain it; you do not recompute it. Monty owns the numbers, you own what they
mean -- and every reading is "agreement with the reference", never "accuracy".

## Loop

Standard tool loop in `core/base_agent.py::run`, up to 6 turns.

## Notes for maintainers

- `score_uncertainty` is where a real propagation or ensemble-spread
  calculation belongs. Until then, Entro is reasoning qualitatively -- treat
  its numbers as calibrated opinion, not measurement.
- Entro is most valuable *after* Monty and *before* Citadel. If Argos routes to
  Citadel without an Entro hop on a consequential decision, that is a routing
  bug worth fixing in `agents/argos/agents.md`.
