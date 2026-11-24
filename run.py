#!/usr/bin/env python
"""CLI entry point.

    python run.py "which SAR product fits a 2024 Bangkok flood mask?"
    python run.py --agent norwood "compare Sentinel-1 GRD vs SLC for flood extent"
    python run.py --list
    python run.py --workflows
    python run.py --no-cache --run-id test-1 "..."   # uncached: writes runs/<id>/ only

The named reports are pipelines, one per mode (core/pipelines/). Several run
in order under ONE run id, so an uncached Flood bot run finds the Flood analyst
modules of the same run folder (D-24):

    python run.py --pipeline flood_analyst,flood_bot,chanakya --experiment kelani-lower-2025
    python run.py --pipeline flood_bot --experiment kelani-lower-2025 --no-agents --cached-only

Monty's sub-graph can be driven directly, with no orchestrator and no API key --
this is the fastest way to exercise receive_input -> resolve_tasks ->
execute_tasks -> return_result:

    python run.py --monty '{"event_id": 42, "tasks": [
        {"function": "fetch_weather",
         "params": {"bbox": [79.94, 6.905, 80.09, 6.978],
                    "start_date": "2025-11-24", "end_date": "2025-12-01",
                    "write_figure": false}}]}'
"""

from __future__ import annotations

import argparse
import json
import sys

from core.graph import SPECIALISTS, run_task
from core.spec import check_all, load_spec, token_report

ALL_AGENTS = ("argos",) + SPECIALISTS


def cmd_list() -> None:
    # Every prompt and every flow card from the vault, or a PromptNoteError
    # naming the note -- the same check the console server runs at start-up.
    loaded = check_all()
    for slug in ALL_AGENTS:
        spec = load_spec(slug)
        print(f"  {slug:<9} {spec.name:<34} slot {spec.model_slot}  "
              f"tools={','.join(spec.tools) or '-'}")
    print(f"\n  {len(loaded)} prompts loaded from the vault")
    # Approximate tokens per agent and mode. No budget is set yet; this is a
    # report, never a cut.
    print("\n  prompt tokens (approx.)")
    for row in token_report():
        print(f"    {row['agent']:<9} {row['mode']:<44} {row['tokens']:>6}")


def cmd_workflows() -> None:
    from core import workflows

    print("Workflows Monty can run:\n")
    print(workflows.catalogue())


def cmd_experiment(slug: str, *, init: bool = False, place: str = "",
                   window: str = "", purge: bool = False,
                   purge_inputs: bool = False) -> int:
    """Create, inspect or purge one event's thesis cache."""
    from core.experiment import Experiment, events

    try:
        run = Experiment(slug)
    except ValueError as exc:
        print(f"  {exc}")
        known = events()
        if known:
            print(f"  existing events: {', '.join(known)}")
        return 1

    if init:
        run.create(place=place, window=window)
        print(f"  created {run.dir}")
    elif not run.memory_path.exists():
        print(f"  no experiment for {slug!r}. Create it with:\n"
              f"    python run.py --experiment {slug} --init "
              f'--place "Derna, Libya" --window "2023-09-08 to 2023-09-14"')
        known = events()
        if known:
            print(f"  existing events: {', '.join(known)}")
        return 1

    if purge:
        # Outputs only unless the inputs are asked for by name. Dropping the
        # inputs means the next run rests on a different fetch of the same
        # thing, which is what this cache exists to prevent.
        removed = run.purge(outputs=True, inputs=purge_inputs)
        print(f"  purged {len(removed)} file(s)"
              + (f": {', '.join(removed)}" if removed else ""))
        if not purge_inputs:
            print("  cached inputs kept -- add --purge-inputs to drop them too")

    print(f"\n  event   : {run.slug}")
    print(f"  memory  : {run.memory_path}")

    cached = run.inputs()
    print(f"\n  Cached inputs ({len(cached)}):")
    for record in cached or []:
        print(f"    {record['name']:<20} {record['bytes']:>9,} B  "
              f"{record['summary'][:44]}")
    if not cached:
        print("    none yet")

    print("\n  Outputs:")
    for row in run.status():
        mark = "written" if row["written"] else "--"
        size = f"{row['bytes']:,} B" if row["written"] else ""
        print(f"    {row['n']:>2}. {row['slot']:<22} {mark:<8} {size}")
    return 0


def cmd_render(slug: str, slot: str) -> int:
    """Render registry-v2 outputs from the experiment's stored results.

    Reads only what is cached: nothing is fetched and no model is called.
    """
    from core import report_render
    from core.experiment import Experiment
    from core.module_blocks import MODULES

    run = Experiment(slug)
    # Every module from its vault template. "all" is Modules 1-10; 11 and 12
    # come after them (D-24), and their agent-written slots stay pending
    # here -- the pipelines fill them.
    for one in (MODULES if slot == "all" else [slot]):
        path = report_render.render(run, one)
        print(f"  wrote {path} and {path.with_suffix('.facts.json').name}")
    return 0


def cmd_pipeline(names: str, slug: str, *, cache: str, run_id: str,
                 agents: bool, cached_only: bool, refresh: str = "") -> int:
    """Run one or more pipelines in order. Agents spend money unless --no-agents."""
    from core import cache_policy, pipelines

    chosen = [n.strip() for n in names.split(",") if n.strip()]
    if not chosen:
        print("--pipeline needs a name", file=sys.stderr)
        return 2
    rid = run_id or cache_policy.new_run_id()
    worst = 0
    for name in chosen:
        try:
            record = pipelines.get(name).run(
                slug, cache=cache, run_id=rid, cached_only=cached_only,
                writer=pipelines.model_writer if agents else None,
                refresh=[t.strip() for t in refresh.split(",") if t.strip()])
        except (KeyError, pipelines.PipelineRefused) as exc:
            print(f"{name}: refused -- {exc}", file=sys.stderr)
            return 2
        print(f"\n{name}: {record['status']} in {record['duration_s']} s "
              f"(run {record['run_id']}, cache {record['cache']})")
        for s in record["steps"]:
            print(f"  {s['n']:>2} {s['kind']:<7} {s['name']:<44} {s['status']:<9} {s['detail'][:90]}")
        print(f"  record: {record['store']}/pipeline_runs/{name}.json")
        if record["status"] == "stopped":
            worst = 1
    return worst


def cmd_single(slug: str, task: str) -> None:
    from importlib import import_module

    agent = import_module(f"agents.{slug}.agent").build()
    print(f"[{agent!r}]\n")
    print(agent.run(task))


def cmd_monty(payload_json: str, attempt: int, truncate: bool) -> int:
    """Run Monty's sub-graph once, directly. No LLM call is made."""
    from core.monty_graph import run_monty

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        print(f"--monty expects a JSON object: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("--monty expects a JSON object with event_id and tasks",
              file=sys.stderr)
        return 2

    state = run_monty(payload, attempt=attempt)
    results = state.get("results") or {}

    print("\n--- results ---")
    for key, result in results.items():
        status = result.get("status")
        head = f"{key}: {result.get('task')} -- {status}"
        print(f"\n{head}")
        if status == "success":
            print(f"  message: {result.get('message')}")
            blob = json.dumps(result.get("output"), indent=2, default=str)
            print("  output : " + (blob[:1200] + "\n  ...[truncated]"
                                   if truncate and len(blob) > 1200 else blob))
        else:
            print(f"  error  : {json.dumps(result.get('error'), indent=2)}")

    failed = [k for k, r in results.items() if r.get("status") != "success"]
    print(f"\n--- {len(results) - len(failed)}/{len(results)} task(s) ok ---")
    return 1 if (failed or state.get("error")) else 0


def cmd_graph(task: str, cache: str = "on", run_id: str = "") -> None:
    state = run_task(task, cache=cache, run_id=run_id)
    print("\n--- exchanges ---")
    for line in state.get("exchange_log", []):
        print(f"  {line}")
    print("\n--- trace ---")
    for hop in state.get("trace", []):
        print(f"  {hop}")
    print("\n--- findings ---")
    for f in state.get("findings", []):
        print(f"\n[{f['agent']}] {f['summary']}")
    print("\n--- answer ---\n")
    print(state.get("answer") or "(Argos returned no final answer)")


def main() -> int:
    parser = argparse.ArgumentParser(description="LangGraph multi-agent runner")
    parser.add_argument("task", nargs="?", help="what you want done")
    parser.add_argument("--agent", choices=ALL_AGENTS,
                        help="run one agent directly, skipping the graph")
    parser.add_argument("--monty", metavar="JSON",
                        help='run Monty\'s sub-graph directly, e.g. '
                             '\'{"event_id": 42, "tasks": [{"function": "...", '
                             '"params": {}}]}\'')
    parser.add_argument("--attempt", type=int, default=1,
                        help="attempt number to report for --monty (default 1)")
    parser.add_argument("--full", action="store_true",
                        help="with --monty, print whole series instead of a head")
    parser.add_argument("--list", action="store_true", help="list agents and exit")
    parser.add_argument("--workflows", action="store_true",
                        help="list the functions Monty can run and exit")
    parser.add_argument("--experiment", metavar="EVENT",
                        help="show the thesis experiment cache for an event "
                             "(omit --init/--purge to just report status)")
    parser.add_argument("--init", action="store_true",
                        help="with --experiment, create the event directory")
    parser.add_argument("--place", default="",
                        help="with --experiment --init, the AOI name")
    parser.add_argument("--window", default="",
                        help="with --experiment --init, the event window")
    parser.add_argument("--purge", action="store_true",
                        help="with --experiment, delete the cached OUTPUTS. "
                             "Add --purge-inputs to drop the inputs too")
    parser.add_argument("--purge-inputs", action="store_true",
                        help="with --experiment --purge, also delete the "
                             "cached workflow inputs")
    parser.add_argument("--no-cache", action="store_true",
                        help="uncached run: every tool re-fetches, and results "
                             "go to runs/<run-id>/ only, never to an experiment "
                             "or the session folder")
    parser.add_argument("--render", metavar="SLOT", nargs="?", const="all",
                        help="with --experiment, render a registry-v2 output "
                             "(or all of them) from what is stored")
    parser.add_argument("--pipeline", metavar="NAME[,NAME]",
                        help="with --experiment, run a mode's pipeline: "
                             "flood_analyst, flood_bot, chanakya (comma list "
                             "runs in order under one run id)")
    parser.add_argument("--no-agents", action="store_true",
                        help="with --pipeline, call no model: agent-written "
                             "parts stay pending")
    parser.add_argument("--cached-only", action="store_true",
                        help="with --pipeline, fetch nothing: a tool whose "
                             "result is not stored is skipped")
    parser.add_argument("--refresh", metavar="TOOL[,TOOL]", default="",
                        help="with --pipeline, re-fetch these tools even though "
                             "a result is stored, and replace it")
    parser.add_argument("--run-id", default="",
                        help="name the run's folder under runs/ (default: a "
                             "UTC stamp plus a random suffix)")
    args = parser.parse_args()
    cache = "off" if args.no_cache else "on"

    if args.list:
        cmd_list()
        return 0
    if args.workflows:
        cmd_workflows()
        return 0
    if args.pipeline:
        if not args.experiment:
            print("--pipeline needs --experiment", file=sys.stderr)
            return 2
        return cmd_pipeline(args.pipeline, args.experiment, cache=cache,
                            run_id=args.run_id, agents=not args.no_agents,
                            cached_only=args.cached_only, refresh=args.refresh)
    if args.experiment and args.render:
        return cmd_render(args.experiment, args.render)
    if args.experiment:
        return cmd_experiment(args.experiment, init=args.init,
                              place=args.place, window=args.window,
                              purge=args.purge, purge_inputs=args.purge_inputs)
    if args.monty:
        from core import cache_policy

        with cache_policy.use(cache, args.run_id) as rid:
            print(f"cache {cache}, run {rid}")
            return cmd_monty(args.monty, args.attempt, truncate=not args.full)
    if not args.task:
        parser.error("a task is required (or use --list / --workflows / --monty)")

    if args.agent:
        cmd_single(args.agent, args.task)
    else:
        cmd_graph(args.task, cache=cache, run_id=args.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
