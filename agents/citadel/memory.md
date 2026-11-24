# Agent Citadel - City Planner - Memory

Persistent journal. Everything above `## Session Log` is re-injected into the
system prompt on every run (see `core/base_agent.py::system_prompt`), so keep
it short and true. The session log goes to `memory.local.md` beside it (gitignored,
append-only, for humans).

Set `MEMORY_WRITE=0` in `.env` to stop the agent writing a session log.

## Active State

- No runs recorded yet.
- `rank_options` is a stub; no MCDM method has been chosen yet.
- Criteria and weights are not yet sourced from the vault -- they are improvised per run.

## Known Constraints

- Tools declared in `agents.md` are stubs until wired up in `core/tools.py`.
- No retrieval layer yet: this agent answers from the model's own knowledge
  plus whatever Argos hands it in the brief.

## Open Questions

- Which MCDM method: weighted sum, TOPSIS, or AHP? The sensitivity output matters more than the ranking.
- Where do the criteria weights come from, and who is allowed to change them?

## Debugging Notes

_Record what broke, what you tried, and what actually fixed it. A note that
says only "fixed" helps nobody._
