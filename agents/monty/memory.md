# Monty - Python expert - Memory

Persistent journal. Everything above `## Session Log` is re-injected into the
system prompt on every run (see `core/base_agent.py::system_prompt`), so keep
it short and true. The session log goes to `memory.local.md` beside it (gitignored,
append-only, for humans).

Set `MEMORY_WRITE=0` in `.env` to stop the agent writing a session log.

## Active State

- Branch `data-ingestion`: Monty no longer fetches code. MCP and Agent M are
  gone; `core/mcp_client.py` was deleted.
- Tasks are first-party functions in `core/workflows/`, called by name. One
  request may carry several; each returns its own `result_N`.
- Registered so far: `fetch_weather_data` (Open-Meteo historical archive).
- Workflows run in-process. `core/sandbox.py` is off the graph path and
  survives only for the chat path's `run_python`.

## Known Constraints

- Argos binds `params` straight to the function signature, so parameter names
  are a public API.
- No wall-clock guard around a task: a workflow with no HTTP timeout can hang
  the batch.
- Open-Meteo's archive trails real time by about five days.

## Open Questions

- Should tasks in a batch run concurrently? They are sequential today, which is
  simplest but slow for several network-bound fetches.
- Does `core/sandbox.py` earn its keep now that nothing fetches code?
- What is the next workflow -- river levels, or one that returns a chart? The
  output contract already allows it.

## Debugging Notes

_Record what broke, what you tried, and what actually fixed it. A note that
says only "fixed" helps nobody._
