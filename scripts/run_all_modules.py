#!/usr/bin/env python
"""Run all twelve modules for one experiment, live, then check what came out.

    python scripts/run_all_modules.py                      # Edinburgh, live, no agents
    python scripts/run_all_modules.py --agents             # ... with agents (SPENDS MONEY)
    python scripts/run_all_modules.py --check <run_id>     # check a finished run only
    python scripts/run_all_modules.py --experiment kelani-lower-2025 --run-id my-run

**Live** means uncached (`cache off`): every tool re-fetches, and the results
go to `runs/<run_id>/` only, never to the experiment. The three pipelines run
in order -- flood_analyst (Modules 1-10), flood_bot (11), chanakya (12) -- each
module rendered from its live vault template. Without `--agents` no model is
called: the land cover brief, the HEV flows and the reports' agent-written
slots stay pending, and nothing is spent. The tools still hit the network.

The check reads the run folder and writes `runs/<run_id>/check.md`:

    pipelines   every step's status; failed, skipped and timed-out steps with why
    modules     each of 1-12 written, its heading, its NOT KNOWN facts with their
                reasons, pending agent slots, placeholders the renderer does not
                know, images that do not resolve, and the wording lint
                (scripts/lint_report.py: untraceable numbers, filler, spelling)
    rasters     every GeoTIFF carries its source and date tags (BUG-020)

Exit 1 when a module is missing, a render step failed, a template names an
unknown block, or an image does not resolve; NOT KNOWN facts and lint issues
are reported, not failed -- a gap stated is the tool working.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

PIPELINES = ("flood_analyst", "flood_bot", "chanakya")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# Test cases the repo does not configure as experiments. Each is registered in
# this process only -- its AOI in report_config, its event.md under
# runs/_test_cases/ (gitignored) -- so nothing in the repo or experiments/
# changes. Edinburgh is the test box of tests/test_openrouter.py, as run before.
CASES_ROOT = REPO / "runs" / "_test_cases"
TEST_CASES = {
    "edinburgh-2026": {"place": "Edinburgh, Scotland (test box)",
                       "window": "2026-08-14 to 2026-08-23",
                       "settings": {"bbox": [-3.25, 55.90, -3.10, 56.00], "country_iso3": "GBR"}},
}


def _root_for(experiment: str) -> Path | None:
    """None for an experiment in experiments/; the test-case root otherwise."""
    from core import report_config
    from core.experiment import Experiment

    if Experiment(experiment).memory_path.exists() or experiment not in TEST_CASES:
        return None
    case = TEST_CASES[experiment]
    report_config.EXPERIMENTS.setdefault(experiment, dict(case["settings"]))
    Experiment(experiment, root=CASES_ROOT).create(place=case["place"], window=case["window"])
    return CASES_ROOT


def run_pipelines(experiment: str, run_id: str, agents: bool) -> str | None:
    """The run id, or None when a pipeline refused to start."""
    from core import cache_policy, pipelines

    root = _root_for(experiment)
    rid = run_id or cache_policy.new_run_id()
    for name in PIPELINES:
        try:
            record = pipelines.get(name).run(experiment, cache="off", run_id=rid, root=root,
                                             writer=pipelines.model_writer if agents else None)
        except pipelines.PipelineRefused as exc:
            print(f"{name}: refused -- {exc}", file=sys.stderr)
            if name == PIPELINES[0]:
                print(f"Nothing ran. Known test cases: {', '.join(TEST_CASES)}; or an "
                      "experiment in experiments/ with a bbox in core/report_config.py.",
                      file=sys.stderr)
                return None
            break
        print(f"{name}: {record['status']} in {record['duration_s']} s (run {rid})")
    return rid


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 3] + "..."


def check(run_id: str, experiment: str) -> int:
    from lint_report import lint

    from core import dating
    from core.cache_policy import RUNS_DIR, RunStore
    from core.experiment import OUTPUTS_V2

    folder = RUNS_DIR / run_id
    if not folder.is_dir():
        print(f"no run folder {folder}", file=sys.stderr)
        return 2
    store = RunStore(run_id, event=experiment)
    out: list[str] = [f"# Check of run `{run_id}` ({experiment})", ""]
    problems: list[str] = []

    # -- pipelines ---------------------------------------------------------------
    out += ["## Pipelines", ""]
    for name in PIPELINES:
        path = folder / "pipeline_runs" / f"{name}.json"
        if not path.exists():
            out.append(f"- **{name}**: no record (did not run)")
            problems.append(f"pipeline {name} has no record")
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for s in rec["steps"]:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        out.append(f"- **{name}**: {rec['status']}, {rec['duration_s']} s, window "
                   f"{' to '.join(rec.get('window') or [])}, bbox {rec.get('bbox')} -- "
                   + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        for s in rec["steps"]:
            if s["status"] in ("failed", "skipped", "timed_out", "not_run", "pending"):
                out.append(f"  - {s['kind']} `{s['name']}` **{s['status']}**: {_short(s['detail'])}")
            if s["kind"] == "render" and s["status"] == "failed":
                problems.append(f"render {s['name']} failed: {_short(s['detail'], 120)}")

    # -- modules -----------------------------------------------------------------
    out += ["", "## Modules", "",
            "| # | Module | Written | NOT KNOWN | Pending slots | Lint |",
            "|---|---|---|---|---|---|"]
    detail: list[str] = []
    for entry in OUTPUTS_V2:
        path = store.path(entry.slot)
        if not path.exists():
            out.append(f"| {entry.n} | {entry.title} | **no** | | | |")
            problems.append(f"Module {entry.n} ({entry.slot}) not written")
            continue
        text = path.read_text(encoding="utf-8")
        heading = next((l for l in text.splitlines() if l.startswith("# ")), "(no heading)")
        side_path = path.with_suffix(".facts.json")
        side = json.loads(side_path.read_text(encoding="utf-8")) if side_path.exists() else {}
        unknown_facts = [f for f in side.get("facts", []) if f.get("status") == "not_known"]
        pending = [s["slot"] for s in side.get("llm_slots", []) if s.get("status") == "pending"]
        bad_ph = side.get("unknown_placeholders") or []
        missing_img = [rel for rel in IMAGE.findall(text)
                       if not (folder / rel).exists() and not Path(rel).exists()]
        lint_result = lint(path)
        lint_cell = "clean" if lint_result["ok"] else ", ".join(
            f"{k} {v}" for k, v in sorted(lint_result["counts"].items()))
        out.append(f"| {entry.n} | {entry.title} | yes | {len(unknown_facts)} | {len(pending)} | {lint_cell} |")
        if bad_ph:
            problems.append(f"Module {entry.n}: the template names unknown blocks {bad_ph}")
        if missing_img:
            problems.append(f"Module {entry.n}: images that do not resolve {missing_img}")
        lines = [f"### Module {entry.n}: `{path.name}`", "", f"Heading: {heading[2:]}", ""]
        if not side:
            lines.append("- no facts sidecar beside it")
        for f in unknown_facts:
            lines.append(f"- NOT KNOWN `{f['key']}`: {_short(f.get('reason', ''))}")
        if pending:
            lines.append(f"- agent slots pending: {', '.join(pending)}")
        for i in lint_result["issues"][:15]:
            where = f"line {i['line']}: " if i["line"] else ""
            lines.append(f"- lint [{i['check']}] {where}{_short(i['detail'], 120)}")
        if len(lint_result["issues"]) > 15:
            lines.append(f"- ... and {len(lint_result['issues']) - 15} more lint issues")
        if len(lines) == 4:
            lines.append("- nothing to report")
        detail += lines + [""]

    # -- rasters -----------------------------------------------------------------
    tifs = sorted(folder.rglob("*.tif"))
    untagged = [t for t in tifs if not dating.read_tags(t).get(dating.TAG_DATED)]
    out += ["", "## Rasters", "",
            f"{len(tifs)} GeoTIFFs; {len(tifs) - len(untagged)} carry source and date tags."]
    out += [f"- untagged: `{t.relative_to(folder)}`" for t in untagged]

    out += ["", "## Problems", ""] + ([f"- {p}" for p in problems] or ["None."])
    out += ["", "## Per module", ""] + detail
    report = folder / "check.md"
    report.write_text("\n".join(out) + "\n", encoding="utf-8")

    for line in out[:out.index("## Per module")]:
        print(line)
    print(f"\nFull check: {report}")
    print(f"Modules:    {folder}")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--experiment", default="edinburgh-2026",
                        help="the experiment whose AOI and window are run (default edinburgh-2026)")
    parser.add_argument("--run-id", default="", help="name the run folder (default: a UTC stamp)")
    parser.add_argument("--agents", action="store_true",
                        help="call the agents for the brief, the HEV flows and the report "
                             "slots -- this SPENDS MONEY")
    parser.add_argument("--check", metavar="RUN_ID", help="only check an existing run folder")
    args = parser.parse_args()
    if args.check:
        return check(args.check, args.experiment)
    if args.agents:
        print("Agents ON: model calls will be made and charged.")
    rid = run_pipelines(args.experiment, args.run_id, args.agents)
    return check(rid, args.experiment) if rid else 2


if __name__ == "__main__":
    sys.exit(main())
