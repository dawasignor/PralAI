#!/usr/bin/env python
"""RQ5: run the mode pipelines N times, then compare the runs.

    python scripts/run_n.py --experiment kelani-lower-2025 \\
        --pipelines flood_analyst,flood_bot,chanakya --n 10 --sleep 120 --no-cache \\
        --batch-id rq5-batch-1
    python scripts/run_n.py --batch-id rq5-batch-1 --compare-only

**Every run spends money** unless `--no-agents`: each one calls the agents of
three pipelines, and `--no-cache` re-fetches every source. Measure one
development run (`--n 1`) and read its cost before starting a batch (Q-3).

* **Strictly sequential**, one run id per run (`<batch_id>-rNN`). The
  pipelines of one run share that id, so an uncached Flood bot run finds the
  Flood analyst modules of the same run folder (D-24).
* **A failing run is recorded and the batch goes on.** A pipeline that is
  refused or crashes is written into the run's record; the next run starts
  after the sleep as planned.
* **Each run is snapshotted into the batch folder** -- its facts sidecars, its
  reports (the text layer, kept and not compared, D-28), its pipeline records
  and its metrics -- so a cached batch, whose store is the experiment and is
  rewritten by every run, still keeps every run apart.
* **Per-run cost and duration come from the RED recorder**
  (`runs/<run_id>/metrics.jsonl`): the agent calls' tokens and reported cost,
  the pipelines' durations. A cost no call reported is "not known", never 0.

`batch.json` is rewritten after every run, so a batch stopped half-way keeps
the runs it finished. An existing batch id is refused: a batch is a record.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core import cache_policy, compare, pipelines, rq5_match   # noqa: E402
from core.experiment import Experiment             # noqa: E402

RESULTS = REPO / "results" / "rq5"
SIDECARS = {"public_report": "11_public_report.facts.json",
            "executive_summary": "12_executive_summary.facts.json"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _code_version() -> str:
    try:
        rev = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"], cwd=REPO,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return rev or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _model_settings() -> dict:
    """Slot 1 as the batch starts: the model and what every call is sent with.
    Never the key or the base URL."""
    try:
        from core.config import get_model_slot   # loads .env on import

        s = get_model_slot(1)
        return {"model": s.model, "temperature": s.temperature, "reasoning": s.reasoning,
                "effort": s.effort if s.reasoning else None, "max_tokens": s.max_tokens,
                "provider_routing": s.provider_routing()}
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def _metrics(rid: str) -> dict:
    """Cost, tokens and durations of one run, from its metrics.jsonl."""
    path = cache_policy.RUNS_DIR / rid / "metrics.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if path.exists() else []
    agents = [r for r in rows if r.get("kind") == "agent"]
    costs = [r.get("cost_usd") for r in agents]
    return {"agent_calls": len(agents),
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in agents),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in agents),
            "cost_usd": round(sum(costs), 6) if costs and None not in costs else None,
            "pipeline_duration_s": round(sum(float(r.get("duration_s") or 0)
                                             for r in rows if r.get("kind") == "pipeline"), 3),
            "metrics_file": path if path.exists() else None}


def _fresh(path: Path, since: float) -> bool:
    return path.exists() and path.stat().st_mtime >= since


def snapshot(store_dir: Path, rid: str, dest: Path, since: float) -> dict:
    """Copy what this run wrote into the batch, and read its failures and
    ranking. Only files written during the run are copied, so a pipeline that
    failed cannot leave the previous run's sidecar standing in for its own."""
    (dest / "facts").mkdir(parents=True, exist_ok=True)
    side = {label: store_dir / name for label, name in SIDECARS.items()}
    side["flood_analyst"] = store_dir / "pipeline_runs" / "flood_analyst.facts.json"
    # Modules 9 and 10 carry agent-written slots too (RQ5 match, core/rq5_match.py).
    for src in sorted(store_dir.glob("[01][0-9]_*.facts.json")):
        label = src.name.split("_", 1)[1].removesuffix(".facts.json")
        side.setdefault(label, src)
    for label, src in side.items():
        if _fresh(src, since):
            shutil.copy2(src, dest / "facts" / f"{label}.facts.json")
    # The tool payloads of this run: what a deliverable was written from, so
    # the RQ5 match can trace its figures (core/rq5_match.py, "any run data").
    # Failure records too (inputs/_failures/): a brief cites the failed scene's figures.
    for src in sorted((store_dir / "inputs").rglob("*.json")):
        if _fresh(src, since):
            target = dest / "inputs" / src.relative_to(store_dir / "inputs")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    # The agents' stored deliverables (Norwood's briefs and hand-overs): text only.
    for src in sorted((store_dir / "deliverables").glob("*.json")):
        if _fresh(src, since):
            (dest / "deliverables").mkdir(exist_ok=True)
            shutil.copy2(src, dest / "deliverables" / src.name)
    for src in sorted(store_dir.glob("[01][0-9]_*.md")):
        if _fresh(src, since):
            (dest / "text").mkdir(exist_ok=True)
            shutil.copy2(src, dest / "text" / src.name)
    for src in sorted((store_dir / "pipeline_runs").glob("*.json")):
        if _fresh(src, since) and not src.name.endswith(".facts.json"):
            (dest / "pipelines").mkdir(exist_ok=True)
            shutil.copy2(src, dest / "pipelines" / src.name)

    # Upstream failures of the tools that ran in THIS run (a reused input
    # carries an older run id and is not this run's measurement).
    source_failures = []
    for path in sorted((store_dir / "inputs").glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run = (rec.get("payload") or {}).get("run") if isinstance(rec.get("payload"), dict) else None
        if run and run.get("run_id") == rid and run.get("source_failures"):
            source_failures.append({"input": rec.get("name"), "tool": rec.get("name"),
                                    "failures": run["source_failures"]})
    ranking = []
    rank = store_dir / "inputs" / "rank_flood_products.json"
    if rank.exists():
        payload = json.loads(rank.read_text(encoding="utf-8")).get("payload") or {}
        ranking = [{"id": r["id"], "rank": r["rank"]} for r in payload.get("ranking") or []]
    return {"source_failures": source_failures, "ranking": ranking}


def run_once(args, rid: str, writer) -> dict:
    started, t0 = _now(), time.time()
    since = t0 - 1.0
    result: dict = {"run_id": rid, "started": started, "pipelines": {}, "tool_failures": []}
    store_dir = None
    for name in args.pipelines:
        try:
            rec = pipelines.get(name).run(args.experiment, cache=args.cache, run_id=rid, writer=writer)
        except pipelines.PipelineRefused as exc:
            result["pipelines"][name] = {"status": "refused", "error": str(exc)[:400]}
            continue
        except Exception as exc:                               # noqa: BLE001
            result["pipelines"][name] = {"status": "crashed",
                                         "error": f"{type(exc).__name__}: {exc}"[:400]}
            continue
        store_dir = Path(rec["store"])
        result["pipelines"][name] = {"status": rec["status"], "duration_s": rec["duration_s"]}
        result["tool_failures"] += [{"pipeline": name, "tool": s["name"], "detail": s["detail"]}
                                    for s in rec["steps"]
                                    if s["kind"] == "tool" and s["status"] in ("failed", "timed_out")]
    if store_dir is None:
        store_dir = (cache_policy.RUNS_DIR / rid if args.cache == "off"
                     else Experiment(args.experiment).dir)
    dest = args.batch_dir / "runs" / rid
    result.update(snapshot(store_dir, rid, dest, since))
    m = _metrics(rid)
    if m.pop("metrics_file") is not None:
        shutil.copy2(cache_policy.RUNS_DIR / rid / "metrics.jsonl", dest / "metrics.jsonl")
    result.update(m, duration_s=round(time.time() - t0, 3), finished=_now())
    (dest / "run.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RQ5: run the pipelines N times and compare")
    p.add_argument("--experiment", default="")
    # RQ5 reports the thesis path, Modules 1-10 (user decision 2026-11-11); the
    # Flood bot and Chanakya reports can still be added by name.
    p.add_argument("--pipelines", default="flood_analyst")
    # RQ5 compares 2 runs (user decision 2026-11-11): one run pair.
    p.add_argument("--n", type=int, default=2)
    p.add_argument("--sleep", type=float, default=120.0, help="seconds between runs")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--no-agents", action="store_true", help="call no model; slots stay pending")
    p.add_argument("--no-run-token", action="store_true",
                   help="do not open each system prompt with the run id (by default it "
                        "does, so no provider cache serves one run from another)")
    p.add_argument("--provider-order", default="deepinfra/fp8",
                   help="OpenRouter provider(s) every call of the batch is pinned to, no "
                        "fallbacks (user decision 2026-11-11: deepinfra/fp8); '' lets "
                        "OpenRouter choose per request")
    p.add_argument("--batch-id", default="")
    p.add_argument("--compare-only", action="store_true",
                   help="re-write the comparison of an existing batch; runs nothing")
    args = p.parse_args(argv)

    args.batch_id = args.batch_id or f"rq5-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    args.batch_dir = RESULTS / args.batch_id
    if args.compare_only:
        if not (args.batch_dir / "batch.json").exists():
            p.error(f"no batch {args.batch_id} at {args.batch_dir}")
        print(f"wrote {compare.write_summary(args.batch_dir)}")
        print(f"wrote {rq5_match.write(args.batch_dir)}  <- the RQ5 result")
        return 0
    if not args.experiment:
        p.error("--experiment is required")
    if (args.batch_dir / "batch.json").exists():
        p.error(f"batch {args.batch_id} exists ({args.batch_dir}); a batch is a record -- "
                "choose another --batch-id, or --compare-only")
    args.pipelines = [n.strip() for n in args.pipelines.split(",") if n.strip()]
    for name in args.pipelines:
        pipelines.get(name)                       # an unknown name fails before any run
    if args.n < 1:
        p.error("--n must be at least 1")
    args.cache = "off" if args.no_cache else "on"
    writer = None if args.no_agents else pipelines.model_writer
    # RQ5 independence: each run's prompts start with its own run id, so no
    # provider cache hit (and no OpenRouter sticky routing) links two runs.
    os.environ["PROMPT_RUN_TOKEN"] = "0" if args.no_run_token else "1"
    # One provider and one precision for every call of every run, for this
    # batch only (the console and the CLI keep whatever .env says).
    if args.provider_order.strip():
        os.environ["MODEL_1_PROVIDER_ORDER"] = args.provider_order.strip()
        os.environ.pop("MODEL_1_ALLOW_FALLBACKS", None)

    batch = {"batch_id": args.batch_id, "experiment": args.experiment,
             "pipelines": args.pipelines, "n": args.n, "sleep_s": args.sleep,
             "cache": args.cache, "agents": not args.no_agents, "code": _code_version(),
             "model_settings": _model_settings(), "run_token": not args.no_run_token,
             "started": _now(), "runs": []}
    args.batch_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, args.n + 1):
        rid = f"{args.batch_id}-r{i:02d}"
        print(f"[{i}/{args.n}] {rid} ...", flush=True)
        try:
            r = run_once(args, rid, writer)
        except Exception as exc:                               # noqa: BLE001
            r = {"run_id": rid, "status": "crashed", "error": f"{type(exc).__name__}: {exc}"[:400]}
        batch["runs"].append({k: r.get(k) for k in ("run_id", "pipelines", "duration_s", "cost_usd",
                                                    "status", "error")})
        (args.batch_dir / "batch.json").write_text(json.dumps(batch, indent=1, default=str),
                                                   encoding="utf-8")
        cost = r.get("cost_usd")
        states = ", ".join(f"{k} {v.get('status')}" for k, v in (r.get("pipelines") or {}).items())
        shown = f"US$ {cost}" if cost is not None else "not known"
        print(f"    {states or r.get('error')} -- {r.get('duration_s')} s, cost {shown}", flush=True)
        if i < args.n:
            time.sleep(args.sleep)
    batch["finished"] = _now()
    (args.batch_dir / "batch.json").write_text(json.dumps(batch, indent=1, default=str), encoding="utf-8")
    print(f"wrote {compare.write_summary(args.batch_dir)}")
    print(f"wrote {rq5_match.write(args.batch_dir)}  <- the RQ5 result")
    return 0


if __name__ == "__main__":
    sys.exit(main())
