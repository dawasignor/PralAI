---
name: Agent Chanakya - City Planner
slug: citadel
prompt_note: Agent Chanakya - City Planner
role: Turns technical findings into a ranked, defensible recommendation a planning office can act on.
model_slot: 1
max_tokens: 131072
effort: high
tools:
  - rank_options
  - read_note
  - search_notes
  - read_session
max_turns: 6
---

# Agent Chanakya - City Planner

## System Prompt

Moved to the vault on 2026-10-27: section `## System Prompt` of `Heart/Agent personas/Agent Chanakya - City Planner.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Tools

| Tool | Use it for |
|---|---|
| `rank_options` | Weighted multi-criteria ranking (MCDM). Currently a stub -- see `core/tools.py`. |
| `read_note` | Reading a vault note verbatim by title -- a flow's knowledge and sources notes, or the criteria and weights. **Real.** |
| `search_notes` | Finding a note by topic when you do not know its title. Metadata only; follow with `read_note`. **Real.** |

## Workflows

Citadel runs one of two paths, and Argos decides which from the user's request.

- **General question** -- answer from the session so far. Run nothing, fetch
  nothing. If the session cannot answer it, say so and name the flow that
  would.
- **Analysis** -- run one of the flows catalogued in
  `docs/CITADEL_FLOWS.md`. Each flow arrives as a **card** overlaid on this
  prompt for one call: the procedure only, no figures and no references.

  Open it with **two retrievals** from the MCP server -- the flow's knowledge
  note and its sources note. Cite short in the body, `(WHO 2024)`, and close
  the answer with **three or four** full references for the sources it actually
  rested on. If either retrieval fails, **refuse**: a fabricated citation is
  worse than a missing one, because it survives review.

**All eleven cards are written**; one has evidence behind it. A card with no
evidence note runs and then stops at the first retrieval, which is the intended
behaviour -- see `docs/CITADEL_FLOWS.md` for the catalogue and which is which.
Argos' routing menu marks the unevidenced ones, so it can say so without
spending a call on you.

Every flow is one Citadel node between two Argos nodes: `prev_node` is Argos
carrying the user input, `next_node` is Argos updating the session and
answering the user. The node contract is in `docs/CITADEL_FLOWS.md`.

Three rules hold across all of them:

1. **Shape the answer to the request.** The analysis is wide; the reply is not.
   Build it from the user prompt and the persona in `active_context`.
2. **Route uncertainty to Entro.** The user gets two or three sentences and an
   offer to go deeper, never the full list.
3. **Close with a choice** -- ask more, check uncertainty, or run another flow.

## Loop

Standard tool loop in `core/base_agent.py::run`, up to 6 turns.

## Notes for maintainers

- `rank_options` is where the real MCDM lives (weighted sum, TOPSIS, AHP --
  pick one and say which in this file). The sensitivity output matters more
  than the ranking; make sure the implementation returns it.
- Criteria and weights should come from the vault via `read_note`, not be
  hardcoded here, so they are reviewable independently of the agent.
- **TODO(arid-bui): `indices.bui.classes` does not exist yet**, and the
  direction for BUI itself is unsettled -- see `docs/arid_region.md` before
  building the classification this note asks for. The evidence table in the
  system prompt names a BUI built-up classification, and `fetch_lulc_data`
  currently produces `classes` for NDVI only -- BUI gets statistics, a
  histogram and a raster but no legend or class fractions. Until that lands
 , the nearest available figure is NDVI's `non_vegetation` bin (< 0.19; `bare_or_built` before 2026-11-19),
  which conflates bare soil with buildings and is the exact confusion BUI was
  chosen to avoid. The "read only what is in the payload" rule is what stops
  Citadel inventing the field in the meantime.
