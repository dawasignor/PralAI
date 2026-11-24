---
name: Agent Argus - Orchestrator
slug: argos
prompt_note: Agent Argus - Orchestrator
role: Decomposes the task, routes it to one specialist at a time, and synthesises the final answer.
model_slot: 1
max_tokens: 131072
effort: high
tools: []
max_turns: 2
---

# Agent Argus - Orchestrator

Argos owns control flow. It is the only node in the graph that decides
anything; the four specialists answer the question they are handed and return.

## System Prompt

Moved to the vault on 2026-10-27: section `## System Prompt` of `Heart/Agent personas/Agent Argus - Orchestrator.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Resolver Prompt

Moved to the vault on 2026-10-27: section `## Resolver Prompt` of `Heart/Agent personas/Agent Argus - Orchestrator.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Debug Prompt

Moved to the vault on 2026-10-27: section `## Debug Prompt` of `Heart/Agent personas/Agent Argus - Orchestrator.md`. Edit it there; core/spec.py loads it through the `prompt_note` line in the frontmatter above.

## Loop

Argos runs once per hop, in `core/graph.py::argos_node`, in one of two modes.

**Routing mode** (`_argos_route_turn`) -- the normal path:

1. Receives task + accumulated findings + remaining budget.
2. Emits a routing decision (JSON), plus `params` when routing to Monty.
3. `_route` sends state to that specialist node, or to `END`.
4. Every specialist edges back to `argos`, so step 1 repeats.

**Debugger mode** (`_argos_debug_turn`) -- entered automatically whenever
`monty_error` is set in state:

1. Receives the error, the parameters sent, and the snippet that ran.
2. Emits a diagnosis and a patch; state carries it to Monty as `monty_debug`.
3. Monty re-runs its sub-graph with the patch applied.
4. On success, `monty_error` clears and routing mode resumes.
5. After `MONTY_MAX_RETRIES` (default 2) retries, `_argos_give_up` writes the
   error itself as the final answer. No LLM call is made for that -- the report
   is assembled from state, so a failure cannot be narrated into a success.

A retry does **not** spend a delegation step: it is the same work, attempted
again. The loop is bounded three ways: `GRAPH_MAX_STEPS` (delegations),
`MONTY_MAX_RETRIES` (retries per Monty job), and LangGraph's `recursion_limit`.
A decision that will not parse is treated as `finish`, not retried.

## Notes for maintainers

- Argos holds no tools on purpose. Give it tools and it starts doing the work
  itself instead of routing.
- **`max_tokens` has to cover the reasoning, not just the output.** Argos emits
  a small JSON object -- a few hundred tokens -- but on an OpenAI-compatible
  endpoint reasoning tokens are charged against the same ceiling. At
  `max_tokens: 8000` with `effort: high`, qwen3.8-flash spent the whole budget
  thinking: the decision came back cut off mid-object with no closing brace,
  the graph could not parse it, and the raw envelope was shown to the user as
  the answer.
  **Raise the ceiling, not the effort** -- the
  rule for this agent is that routing quality is the whole value of
  the node, so `effort: high` stays and `max_tokens` moves. It is 24000 now.
  This is the first thing to check when a new model starts returning
  unparseable decisions; `core/base_agent.py` marks a truncated reply and
  `_parse_decision` refuses to show it as an answer, so the symptom is now a
  clear message rather than raw JSON.
- To add a specialist: add the slug to `SPECIALISTS` in `core/graph.py`, add a
  row to the table above, and create `agents/<slug>/`.
- `## Debug Prompt` and `## Resolver Prompt` are optional sections that
  `core/spec.py` loads into `AgentSpec.debug_prompt` and
  `AgentSpec.resolver_prompt`, and `BaseAgent.run(extra_system=...)` overlays
  for a single call. Any agent can have one; only Argos uses either today.
- The resolver runs before routing and decides nothing about routing. It reads
  language; `core/resolve.py` decides which candidate wins and whether the
  result is good enough to proceed on. Those are rules, and rules belong
  somewhere a test can reach them.
- The give-up report is built in Python, not by the model, on purpose. Asking
  an LLM to summarise its own three failed attempts is how "it failed" becomes
  "it partially succeeded".
