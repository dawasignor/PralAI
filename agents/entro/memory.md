# Entro - Uncertainty Analyser - Memory

Persistent journal. Everything above `## Session Log` is re-injected into the
system prompt on every run (see `core/base_agent.py::system_prompt`), so keep
it short and true. The session log goes to `memory.local.md` beside it (gitignored,
append-only, for humans).

Set `MEMORY_WRITE=0` in `.env` to stop the agent writing a session log.

## Active State

- No runs recorded yet.
- `score_uncertainty` is a stub, so all confidence numbers so far are qualitative judgement, not computation.
- Uncertainty taxonomy in use: aleatoric / epistemic / structural.

## Known Constraints

- Tools declared in `agents.md` are stubs until wired up in `core/tools.py`.
- No retrieval layer yet: this agent answers from the model's own knowledge
  plus whatever Argos hands it in the brief.

## Open Questions

- What calibration set could turn Entro's 0-1 confidence into something checkable?
- Should Entro run on every graph path, or only when a decision is consequential?

## Debugging Notes

_Record what broke, what you tried, and what actually fixed it. A note that
says only "fixed" helps nobody._
