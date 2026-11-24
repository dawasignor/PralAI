# Agent Argos - Orchestrator - Memory

Persistent journal. Everything above `## Session Log` is re-injected into the
system prompt on every run (see `core/base_agent.py::system_prompt`), so keep
it short and true. The session log goes to `memory.local.md` beside it (gitignored,
append-only, for humans).

Set `MEMORY_WRITE=0` in `.env` to stop the agent writing a session log.

## Active State

- No runs recorded yet.
- Routing order in use: norwood -> monty -> entro -> citadel, skipping hops the task does not need.
- Delegation budget comes from `GRAPH_MAX_STEPS` (default 8).
- Two modes: routing, and debugger (entered automatically when `monty_error` is
  set). The debugger persona is the `## Debug Prompt` section of agents.md.
- Monty retry budget is `MONTY_MAX_RETRIES` (default 2, so three attempts).
  A retry does not spend a delegation step.
- Monty now takes `{event_id, tasks}` -- a batch of registered function
  calls. The catalogue is injected into the routing prompt, so never guess
  a name.
- On failure Argos patches the whole task list (`patched_tasks`), and may
  drop a task that cannot be fixed.

## Known Constraints

- Tools declared in `agents.md` are stubs until wired up in `core/tools.py`.
- No retrieval layer yet: this agent answers from the model's own knowledge
  plus whatever Argos hands it in the brief.

## Open Questions

- Should Argos be allowed to fan out to two specialists in parallel, or does serial routing stay a hard rule?
- What is the right budget for a full four-hop analysis versus a one-hop lookup?
- Should the retry contract generalise beyond Monty, or is execution the only
  place where a mechanical retry makes sense?

## Debugging Notes

_Record what broke, what you tried, and what actually fixed it. A note that
says only "fixed" helps nobody._
