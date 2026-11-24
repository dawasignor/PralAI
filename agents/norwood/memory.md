# Agent Norwood - Satellite expert - Memory

Persistent journal. Everything above `## Session Log` is re-injected into the
system prompt on every run (see `core/base_agent.py::system_prompt`), so keep
it short and true. The session log goes to `memory.local.md` beside it (gitignored,
append-only, for humans).

Set `MEMORY_WRITE=0` in `.env` to stop the agent writing a session log.

## Active State

- No runs recorded yet.
- No catalogue connected: every dataset id this agent produces is unverified.
- Intended knowledge source is the local Obsidian vault, not yet wired to `read_note`.

## Known Constraints

- Tools declared in `agents.md` are stubs until wired up in `core/tools.py`.
- No retrieval layer yet: this agent answers from the model's own knowledge
  plus whatever Argos hands it in the brief.

## Open Questions

- Which catalogue should `search_datasets` hit first -- STAC, CMR, or the provider API?
- How should the tier ladder (near-real-time / pretrained / raw) be represented in a finding so Citadel can weigh it?

## Debugging Notes

_Record what broke, what you tried, and what actually fixed it. A note that
says only "fixed" helps nobody._
