"""RQ5: how much do N runs of the same pipelines agree?

Reads one batch folder written by `scripts/run_n.py`:

    results/rq5/<batch_id>/
        batch.json                     the batch: pipelines, N, cache, code version
        runs/<run_id>/run.json         one run: statuses, duration, cost, failures, ranking
        runs/<run_id>/facts/*.json     its facts sidecars (flood_analyst, public_report,
                                       executive_summary)
        runs/<run_id>/text/*.md        its reports -- kept, NOT compared (D-28)

and writes `summary.md`, `compare.json` and `figures/` beside them.

**The facts layer is what is compared**, fact by fact, per sidecar:

* numeric facts: mean, standard deviation (sample, n-1), coefficient of
  variation, range and the maximum absolute deviation from the mean;
* categorical facts (status chips, the product choice, lists): agreement rate
  (the share of run pairs that agree) and the modal share;
* the ranking: pairwise Spearman rho and Kendall's W (with the tie correction),
  in plain Python -- no scipy.

**Source failures are kept apart from disagreements.** A run whose tool hit an
Overpass 502 and returned less did not disagree; it measured less. Each
observation of a fact is attributed to the tools named in its `source`; when
one of those tools failed or had a failed upstream call in that run, the
observation is counted under "source failure", not in the spread. A fact whose
source names no tool cannot be attributed, and the summary says so.

**A fact that is NOT KNOWN in a run is not a value.** The spread is over the
runs where it is known; how many runs knew it is reported beside it, because a
fact known in 6 of 10 runs agreeing perfectly is not a fact that agrees.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

LABELS = ("flood_analyst", "public_report", "executive_summary")

# A source string that names no registered tool, mapped to the tool behind it.
SOURCE_TOOLS = {"World Bank": "fetch_world_bank_indicators", "INFORM": "fetch_inform_risk",
                "GDACS": "search_previous_disasters", "OpenStreetMap": "where_am_i"}


# -- reading a batch -------------------------------------------------------------
def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_batch(batch_dir: Path) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    batch = _read(batch_dir / "batch.json")
    runs = []
    for entry in batch.get("runs") or []:
        folder = batch_dir / "runs" / entry["run_id"]
        run = _read(folder / "run.json") if (folder / "run.json").exists() else dict(entry)
        run["facts"] = {}
        for label in LABELS:
            side = folder / "facts" / f"{label}.facts.json"
            if side.exists():
                run["facts"][label] = {f["key"]: f for f in _read(side).get("facts") or []}
        runs.append(run)
    batch["runs"] = runs
    return batch


# -- attribution -------------------------------------------------------------------
def _tool_names() -> set[str]:
    try:
        from . import workflows

        names = set(workflows.names(include_inactive=True))
        return names | {workflows.cache_name(n) for n in names}
    except Exception:                                          # noqa: BLE001
        return set()


def _with_cache_names(names: set[str]) -> set[str]:
    """A tool is named by its registered name in a fact's source and by its
    cache name in a stored input; both spellings, so the two can meet."""
    try:
        from . import workflows

        # flood_map_<id> (one stored map per product) belongs to get_flood_map.
        return (names | {workflows.cache_name(n) for n in names if n}
                | {workflows.tool_of(n) for n in names if n})
    except Exception:                                          # noqa: BLE001
        return names


def tools_of(source: str, known: set[str] | None = None) -> set[str]:
    """The tools a fact's `source` names, as registered and cache names."""
    known = _tool_names() if known is None else known
    words = set(re.findall(r"[a-z][a-z0-9_]+", source or ""))
    found = {w for w in words if w in known}
    found |= {tool for text, tool in SOURCE_TOOLS.items() if text in (source or "")}
    return _with_cache_names(found)


def failing_tools(run: dict) -> set[str]:
    """Tools (registered and cache names) that failed, or had a failed
    upstream call, in this run."""
    out = set()
    for f in run.get("source_failures") or []:
        out |= {f.get("tool", ""), f.get("input", "")}
    for f in run.get("tool_failures") or []:
        out.add(f.get("tool", ""))
    return _with_cache_names({t for t in out if t})


# -- statistics ----------------------------------------------------------------------
def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def numeric_stats(values: list[float]) -> dict[str, Any]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    return {"kind": "numeric", "n": n, "mean": mean, "sd": sd,
            "cv": (sd / abs(mean)) if mean else None, "min": min(values), "max": max(values),
            "range": max(values) - min(values),
            "max_abs_dev": max(abs(v - mean) for v in values),
            "identical": len(set(values)) == 1}


def categorical_stats(values: list[str]) -> dict[str, Any]:
    n = len(values)
    counts = Counter(values)
    modal, top = counts.most_common(1)[0]
    pairs = n * (n - 1) / 2
    agree = sum(c * (c - 1) / 2 for c in counts.values())
    return {"kind": "categorical", "n": n, "modal": modal, "modal_share": top / n,
            "agreement_rate": (agree / pairs) if pairs else 1.0, "distinct": len(counts),
            "identical": len(counts) == 1}


def _canon(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True, default=str)


def compare_facts(runs: list[dict], label: str) -> dict[str, dict[str, Any]]:
    """Per fact of one sidecar: its statistics over the clean runs, how many
    runs knew it, and the observations set aside for a source failure."""
    known_tools = _tool_names()
    keys = sorted({k for r in runs for k in (r.get("facts") or {}).get(label, {})})
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        clean, failed_obs, not_known, missing = [], [], [], 0
        for r in runs:
            fact = (r.get("facts") or {}).get(label, {}).get(key)
            if fact is None:
                missing += 1
                continue
            if fact.get("status") == "not_known":
                not_known.append(fact.get("reason", ""))
                continue
            tools = tools_of(fact.get("source", ""), known_tools)
            if tools & failing_tools(r):
                failed_obs.append(r["run_id"])
                continue
            clean.append(fact.get("value"))
        row: dict[str, Any] = {"runs": len(runs), "known": len(clean) + len(failed_obs),
                               "not_known": len(not_known), "absent": missing,
                               "set_aside_for_source_failure": failed_obs,
                               "not_known_reasons": sorted(set(not_known))[:3]}
        if clean and all(_is_number(v) for v in clean):
            # The values themselves are kept: the spread figure plots them.
            row.update(numeric_stats([float(v) for v in clean]), values=[float(v) for v in clean])
        elif clean:
            row.update(categorical_stats([_canon(v) for v in clean]))
        else:
            row.update({"kind": "none", "n": 0, "identical": None})
        out[key] = row
    return out


# -- rankings ------------------------------------------------------------------------
def _avg_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Spearman rho over the items both rankings hold (Pearson on average ranks)."""
    items = sorted(set(a) & set(b))
    if len(items) < 2:
        return None
    x, y = _avg_ranks([a[i] for i in items]), _avg_ranks([b[i] for i in items])
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y))
    sx = math.sqrt(sum((p - mx) ** 2 for p in x))
    sy = math.sqrt(sum((q - my) ** 2 for q in y))
    return sxy / (sx * sy) if sx and sy else None


def kendall_w(rankings: list[dict[str, float]]) -> float | None:
    """Kendall's coefficient of concordance over the items every ranking holds,
    with the correction for ties."""
    if len(rankings) < 2:
        return None
    items = sorted(set.intersection(*(set(r) for r in rankings)))
    n, m = len(items), len(rankings)
    if n < 2:
        return None
    ranked = [_avg_ranks([r[i] for i in items]) for r in rankings]
    totals = [sum(col[j] for col in ranked) for j in range(n)]
    mean = m * (n + 1) / 2
    s = sum((t - mean) ** 2 for t in totals)
    ties = sum(c ** 3 - c for col in ranked for c in Counter(col).values())
    denom = m ** 2 * (n ** 3 - n) - m * ties
    return 12 * s / denom if denom else None


def compare_rankings(runs: list[dict]) -> dict[str, Any]:
    rankings = {r["run_id"]: {x["id"]: float(x["rank"]) for x in r.get("ranking") or []}
                for r in runs if r.get("ranking")}
    pairs = {f"{a} vs {b}": spearman(rankings[a], rankings[b])
             for a, b in combinations(sorted(rankings), 2)}
    rhos = [v for v in pairs.values() if v is not None]
    products = sorted({p for r in rankings.values() for p in r})
    places = max((len(r) for r in rankings.values()), default=0)
    freq = {p: [sum(1 for r in rankings.values() if r.get(p) == k) for k in range(1, places + 1)]
            for p in products}
    top = Counter(min(r, key=r.get) for r in rankings.values() if r)
    return {"runs_with_ranking": len(rankings), "spearman_pairs": pairs,
            "spearman_min": min(rhos) if rhos else None,
            "spearman_mean": sum(rhos) / len(rhos) if rhos else None,
            "kendall_w": kendall_w(list(rankings.values())),
            "rank_frequency": freq, "top_choice": dict(top)}


# -- the whole batch ---------------------------------------------------------------
def compare(batch_dir: Path) -> dict[str, Any]:
    batch = load_batch(batch_dir)
    runs = batch["runs"]
    facts = {label: compare_facts(runs, label) for label in LABELS
             if any(label in (r.get("facts") or {}) for r in runs)}
    return {"batch": {k: v for k, v in batch.items() if k != "runs"},
            "runs": [{k: v for k, v in r.items() if k != "facts"} for r in runs],
            "facts": facts, "ranking": compare_rankings(runs)}


def _fmt(v: Any, places: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if v == 0:
            return "0"
        if abs(v) >= 1000:                     # a population, not 3.106e+05
            return f"{v:,.1f}".rstrip("0").rstrip(".")
        return f"{v:.{places}g}" if abs(v) >= 1e-4 else f"{v:.2e}"
    return str(v)


def summary_markdown(result: dict[str, Any], figures: list[str]) -> str:
    b, runs = result["batch"], result["runs"]
    lines = [f"# RQ5 batch {b.get('batch_id')}", "",
             f"- experiment: {b.get('experiment')}",
             f"- pipelines: {', '.join(b.get('pipelines') or [])}",
             f"- runs: {len(runs)} of {b.get('n')} planned; cache {b.get('cache')}; "
             f"agents {'on' if b.get('agents') else 'off'}; sleep {b.get('sleep_s')} s",
             f"- code: {b.get('code')}", f"- started {b.get('started')}, finished {b.get('finished', 'n/a')}",
             "", "## Runs", "",
             "| Run | Pipelines | Duration (s) | Cost (US$) | Tokens in / out | Source failures | Tool failures |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for r in runs:
        st = ", ".join(f"{k} {v.get('status')}" for k, v in (r.get("pipelines") or {}).items())
        lines.append(f"| {r['run_id']} | {st} | {_fmt(r.get('duration_s'))} | "
                     f"{_fmt(r.get('cost_usd'))} | {r.get('input_tokens', 0)} / {r.get('output_tokens', 0)} | "
                     f"{len(r.get('source_failures') or [])} | {len(r.get('tool_failures') or [])} |")
    costs = [r.get("cost_usd") for r in runs]
    lines += ["", ("Total cost: US$ " + _fmt(sum(costs)) if costs and None not in costs else
                   "Total cost: not known -- at least one agent call reported no cost (or none ran).")]

    for label, table in result["facts"].items():
        num = {k: v for k, v in table.items() if v["kind"] == "numeric"}
        cat = {k: v for k, v in table.items() if v["kind"] == "categorical"}
        none = {k: v for k, v in table.items() if v["kind"] == "none"}
        lines += ["", f"## Facts: {label.replace('_', ' ')}", "",
                  f"{sum(1 for v in table.values() if v.get('identical'))} of {len(table)} facts "
                  "identical across the runs that knew them.", ""]
        if num:
            lines += ["| Fact | Known | Mean | SD | CV | Range | Max abs dev |",
                      "|---|---:|---:|---:|---:|---:|---:|"]
            lines += [f"| {k} | {v['known']}/{v['runs']} | {_fmt(v['mean'])} | {_fmt(v['sd'])} | "
                      f"{_fmt(v['cv'])} | {_fmt(v['range'])} | {_fmt(v['max_abs_dev'])} |"
                      for k, v in num.items()]
        if cat:
            lines += ["", "| Fact | Known | Agreement rate | Modal share | Distinct values |",
                      "|---|---:|---:|---:|---:|"]
            lines += [f"| {k} | {v['known']}/{v['runs']} | {_fmt(v['agreement_rate'], 3)} | "
                      f"{_fmt(v['modal_share'], 3)} | {v['distinct']} |" for k, v in cat.items()]
        if none:
            lines += ["", "Not known in any run: " + ", ".join(sorted(none)) + "."]
        aside = {k: v["set_aside_for_source_failure"] for k, v in table.items()
                 if v["set_aside_for_source_failure"]}
        if aside:
            lines += ["", "Set aside for a source failure (not counted in the spread):"]
            lines += [f"- {k}: {', '.join(v)}" for k, v in aside.items()]

    rk = result["ranking"]
    lines += ["", "## Ranking", "",
              f"- runs with a ranking: {rk['runs_with_ranking']}",
              f"- Spearman rho, pairwise: min {_fmt(rk['spearman_min'])}, mean {_fmt(rk['spearman_mean'])}",
              f"- Kendall's W: {_fmt(rk['kendall_w'])}",
              f"- best-ranked product: " + (", ".join(f"{p} in {c}" for p, c in rk["top_choice"].items())
                                            or "n/a")]

    lines += ["", "## Source failures", ""]
    sf = [(r["run_id"], f) for r in runs for f in (r.get("source_failures") or [])]
    tf = [(r["run_id"], f) for r in runs for f in (r.get("tool_failures") or [])]
    if not sf and not tf:
        lines.append("None recorded.")
    lines += [f"- {rid}: {f.get('tool') or f.get('input')} -- "
              + "; ".join(f"{x.get('domain')} {x.get('status', '')} {x.get('reason', '')}".strip()
                          for x in f.get("failures") or []) for rid, f in sf]
    lines += [f"- {rid}: {f.get('pipeline')} step {f.get('tool')} failed -- {f.get('detail', '')[:160]}"
              for rid, f in tf]

    lines += ["", "## What this comparison does not cover", "",
              "- The text layer is not compared (D-28). Every run's reports are kept under "
              "`runs/<run_id>/text/`, so it can be added later.",
              "- A fact whose source names no tool cannot be attributed to a source failure; "
              "its spread includes every run that knew it.",
              "- A run that failed or was refused contributes no facts; it is listed above."]
    if figures:
        lines += ["", "## Figures", ""] + [f"![{Path(f).stem}](figures/{Path(f).name})" for f in figures]
    return "\n".join(lines) + "\n"


def write_summary(batch_dir: Path) -> Path:
    from .charts_rq5 import draw_all

    batch_dir = Path(batch_dir)
    result = compare(batch_dir)
    (batch_dir / "compare.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    figures = draw_all(result, batch_dir / "figures")
    path = batch_dir / "summary.md"
    path.write_text(summary_markdown(result, [str(f) for f in figures]), encoding="utf-8")
    return path
