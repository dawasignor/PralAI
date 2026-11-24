---
name: Agent Norwood - Satellite expert
slug: norwood
prompt_note: Agent Norwood - Satellite expert
role: Earth-observation domain expert. Knows what each sensor and product can and cannot show.
model_slot: 1
max_tokens: 131072
effort: high
tools:
  - search_datasets
  - read_note
  - search_notes
  - read_session
  - read_monty_payload
  - geocode_place
max_turns: 14
---

# Agent Norwood - Satellite expert

## System Prompt

Moved to the vault on 2026-10-27: section `## System Prompt` of `Heart/Agent personas/Agent Norwood - Satellite expert.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Tools

| Tool | Use it for |
|---|---|
| `search_datasets` | Candidate products for a sensor / AOI / time window. Currently a stub -- see `core/tools.py`. |
| `read_note` | Reading a vault note verbatim by title. **Real** -- reads the Obsidian vault through `core/vault.py`. |
| `search_notes` | Finding a note by topic when you do not know its title. Metadata only; follow with `read_note`. **Real.** |

`search_datasets` is still a stub -- while it is, answer dataset questions from
your own knowledge and mark them "needs verification". `read_note` and
`search_notes` are real and read the live vault, so anything you take from a
note is sourced and should be attributed to it.

## Loop

Standard tool loop in `core/base_agent.py::run`: prompt -> model turn ->
execute any `tool_use` blocks -> feed all results back in one user message ->
repeat until `end_turn` or `max_turns` (8).

**The last turn is reserved for your answer** -- no tools are offered on it, so
spend the earlier ones deciding what you actually need. Reading four notes in
full and running out of budget produces nothing; two notes and an answer is a
finding. If you reach it with your research unfinished, the reply is marked as
resting on incomplete evidence, which Entro will see.

## Notes for maintainers

**`max_tokens` is 131072 (128k) for every agent since 2026-11-12** (user:
"Remove the 32000 token limit. It is truncating the inference given by the
agents" -- a Kandy session reply was cut at 32000 that day). It is not omitted:
OpenRouter does not document the default when `max_tokens` is absent, and a
provider default can be lower. 128k is about twice the largest reply ever
recorded (64k across turns) and below every provider's output maximum for the
model (>= 262k); only generated tokens are billed.

**Earlier: 32000, raised from 16000 on 2026-09-22.** The
`pre_flood_aoi_brief` flow emits a 16-section analyst contract, and at 16000
the reply stopped after seven sections -- every forward-looking part, including
`findings` and `flood_mapping_outlook`, was simply absent. Reasoning tokens
come out of the same budget on an OpenAI-compatible endpoint, so a long
structured output needs far more headroom than its own length suggests.

**Raise the ceiling, not the effort.** `effort: high` is what makes this agent
worth asking; the same rule holds for Argus, which hit this
first at 8000 and now runs at 24000. If a brief truncates again, the next move
is more ceiling, not less thinking.

The failure was visible only because `BaseAgent.run` marks a `max_tokens` stop
with a `[TRUNCATED: ...]` note. Without it the seven-section fragment would
have been stored as a finished brief.

- Wire `search_datasets` to a real catalogue (STAC / CMR / Copernicus) before
  trusting any dataset id that comes out of this agent.
- The knowledge vault is `<VAULT_ROOT>`, set by `VAULT_ROOT`.
