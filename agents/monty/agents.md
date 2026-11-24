---
name: Monty - Python expert
slug: monty
prompt_note: Monty - Python expert
role: Runs registered data-ingestion and analysis workflows on request, and reviews Python when asked directly.
model_slot: 1
max_tokens: 131072
effort: high
tools:
  - run_python
  - read_session
max_turns: 8
---

# Monty - Python expert

Monty has two faces. Inside the graph it is **not** a chat node: the `monty`
node runs a four-step sub-graph that executes registered functions by name. The
system prompt below is used only when Monty is called directly
(`python run.py --agent monty "..."`) for code review and debugging advice.

## Graph

```
[Argos: event_id + task list]
      |
      v
(1) Receive Input --> (2) Resolve Tasks --> (3) Execute Tasks --> (4) Return Result
      |                      |                     |                     |
      +--- bad payload ------+---------------------+---------------------+
                                                                         v
                                                        [Argos: reads result_1..N]
```

Implemented in `core/monty_graph.py`. Each node owns one state channel and
overwrites it; `execution_logs` and `exchange_log` append.

| # | Node | Does | Writes |
|---|---|---|---|
| 1 | `receive_input` | Validates the payload; applies a retry's `patched_tasks` | `tasks`, `timestamp`, `error` |
| 2 | `resolve_tasks` | Looks every function name up in the registry | `resolved` |
| 3 | `execute_tasks` | Runs each task in order, isolating failures | `results`, `error` |
| 4 | `return_result` | Reports OK / PARTIAL / ERROR to Argos | `results` |

**There is no MCP and no Agent M.** Monty owns the code it runs.

### Request

Argos sends an `event_id` and a list of tasks -- effectively a batch of tool
calls in one request:

```json
{"event_id": 42,
 "tasks": [
   {"function": "fetch_weather",
    "params": {"bbox": [79.94, 6.905, 80.09, 6.978],
               "start_date": "2025-11-24", "end_date": "2025-12-01"}}
 ]}
```

`params` are bound to the function signature directly, so **parameter names are
the contract**. A mismatch surfaces as `signature_mismatch`.

### Response

One entry per request, in order. Each carries the output and either an error or
a success message naming the task:

```json
{"result_1": {"task": "fetch_weather",
              "output": {"meta": {}, "hourly": {}, "...": {}},
              "status": "success",
              "message": "fetch_weather completed successfully",
              "error": null},
 "result_2": {"task": "fetch_river_levels",
              "output": null,
              "status": "error",
              "error": {"error_type": "unknown_function", "message": "..."}}}
```

**Failures are isolated.** One task raising does not stop the batch; every task
still gets a `result_N`. The batch is reported as an error only if at least one
task failed, which is what puts Argos' debugger on.

### Registry

`core/workflows/` holds the functions. `python run.py --workflows` lists them.

| Workflow | Purpose |
|---|---|
| `fetch_weather` | Gridded event weather over a bounding box from the Open-Meteo archive (the one weather tool since 2026-10-25; `fetch_weather_data` is retired). See [docs/fetch_weather_grid.md](../../docs/fetch_weather_grid.md). |
| `where_am_i` | What an AOI is: map, admin units, counts of buildings (houses and apartments included) and facilities, the facilities located, and water influences. A place name becomes a `bbox` before routing (core/resolve.py); `fetch_place_context` was retired on 2026-10-25. See [docs/where_am_i.md](../../docs/where_am_i.md). |

Adding one: write the function, decorate it with `@workflow(name, description)`,
and import its module in `core/workflows/__init__.py`. Argos picks it up
automatically -- the catalogue is injected into its routing prompt.

### Error vocabulary

Every failure is returned as a structured value, never raised out of the batch.
These names are what Argos' debugger persona reasons over -- do not invent new
ones without adding them to `agents/argos/agents.md` too.

| `error_type` | Meaning |
|---|---|
| `bad_payload` | No tasks, or a task with no `function` key. Batch-level. |
| `unknown_function` | Name is not in the registry. |
| `signature_mismatch` | `params` do not fit the function signature. |
| `runtime_error` | The function raised. |
| `unserialisable` | The function returned something JSON cannot represent. |

### Retries

Monty does not retry itself. It reports to Argos, which patches the task list
and re-sends. After `MONTY_MAX_RETRIES` (default 2) the error becomes the final
output -- along with any results that did succeed.

### Driving it directly

```bash
python run.py --workflows
python run.py --monty '{"event_id": 42, "tasks": [{"function": "fetch_weather", "params": {"bbox": [79.94, 6.905, 80.09, 6.978], "start_date": "2025-11-24", "end_date": "2025-12-01", "write_figure": false}}]}'
```

No LLM call, no API key -- but `fetch_weather` does make a real HTTP
request to Open-Meteo.

## System Prompt

Moved to the vault on 2026-10-27: section `## System Prompt` of `Heart/Agent personas/Monty - Python expert.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Tools

| Tool | Use it for |
|---|---|
| `run_python` | Executing a short snippet to check a result. Currently a stub -- it does **not** execute anything. |

`run_python` is intentionally inert, and belongs to the *chat* path only. The
*graph* path does not execute arbitrary code at all any more -- it calls
registered first-party functions. Do not present `run_python` output as if the
code ran.

## Loop

Two loops, depending on how Monty was entered.

**Graph path** -- `core/monty_graph.py`, four nodes, no model call at all. See
`## Graph` above. This is what the `monty` node in `core/graph.py` invokes.

**Chat path** -- the standard tool loop in `core/base_agent.py::run`, up to 8
turns; more than the other agents because a write / run / fix cycle needs the
room. Used by `python run.py --agent monty "..."`.

## Notes for maintainers

- Workflows run **in this process**, directly. That is safe because they are
  first-party code in `core/workflows/`, reviewed like any other module -- not
  fetched, not generated. If that ever stops being true, the execution model
  has to change with it.
- Workflows that hit the network own their own timeout. There is no wall-clock
  guard around a task, so a library with no timeout can hang the batch.
- `core/sandbox.py` is no longer on the graph path. It survives for the chat
  path's `run_python`; delete it if that stays a stub.
- Before implementing the chat path's `run_python`, reuse `core/sandbox.py`
  rather than writing a second executor. Do not `exec()` model-generated code
  in this process -- the in-process rule above covers reviewed workflows only.
