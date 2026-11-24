"""The RQ5 comparison figures, drawn from `core.compare.compare()`'s result.

Like core/charts.py: every mark is read from the result, nothing recomputed,
so a figure cannot disagree with the summary table beside it. White
backdrop, the decision outputs' blue and rust.

    numeric_spread.png   each numeric fact's runs, as deviation from its batch mean
    chip_agreement.png   each categorical fact's modal share
    rank_frequency.png   how often each product took each rank
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .charts import MARK, ORDER_BAND


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white", bbox_inches="tight")
    _plt().close(fig)
    return path


def numeric_spread(result: dict[str, Any], path: Path) -> Path | None:
    rows = [(f"{label}: {k}", v["values"]) for label, table in result["facts"].items()
            for k, v in table.items() if v["kind"] == "numeric" and v.get("values")]
    if not rows:
        return None
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(rows) + 1.2), dpi=130)
    fig.patch.set_facecolor("white")
    for y, (key, vals) in enumerate(rows):
        mean = sum(vals) / len(vals)
        dev = [((v - mean) / abs(mean) * 100) if mean else (v - mean) for v in vals]
        jitter = [((i % 5) - 2) * 0.06 for i in range(len(dev))]
        ax.scatter(dev, [y + j for j in jitter], s=18, color=ORDER_BAND, zorder=3)
        if max(dev) != min(dev):
            ax.plot([min(dev), max(dev)], [y, y], color=MARK, lw=1, zorder=2)
    ax.axvline(0, color="#888888", lw=0.6)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([k for k, _ in rows], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("deviation from the batch mean, % of the mean (absolute when the mean is 0)", fontsize=8)
    ax.set_title(f"Numeric facts across {len(result['runs'])} runs (rust: the range)", fontsize=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return _save(fig, path)


def chip_agreement(result: dict[str, Any], path: Path) -> Path | None:
    rows = [(f"{label}: {k}", v["modal_share"]) for label, table in result["facts"].items()
            for k, v in table.items() if v["kind"] == "categorical"]
    if not rows:
        return None
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 0.36 * len(rows) + 1.2), dpi=130)
    fig.patch.set_facecolor("white")
    ax.barh(range(len(rows)), [s * 100 for _, s in rows],
            color=[ORDER_BAND if s == 1 else MARK for _, s in rows])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([k for k, _ in rows], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("modal share, % of the runs that knew the fact (rust: below 100)", fontsize=8)
    ax.set_title("Categorical facts: how often the runs gave the same answer", fontsize=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return _save(fig, path)


def rank_frequency(result: dict[str, Any], path: Path) -> Path | None:
    freq = result["ranking"]["rank_frequency"]
    if not freq:
        return None
    plt = _plt()
    products = sorted(freq, key=lambda p: [-c for c in freq[p]])
    places = len(next(iter(freq.values())))
    grid = [freq[p] for p in products]
    fig, ax = plt.subplots(figsize=(1.1 * places + 2.5, 0.5 * len(products) + 1.3), dpi=130)
    fig.patch.set_facecolor("white")
    ax.imshow(grid, cmap="Blues", vmin=0, vmax=max(1, result["ranking"]["runs_with_ranking"]))
    for i, row in enumerate(grid):
        for j, c in enumerate(row):
            ax.text(j, i, str(c), ha="center", va="center", fontsize=9,
                    color="white" if c > result["ranking"]["runs_with_ranking"] / 2 else "#222222")
    ax.set_xticks(range(places))
    ax.set_xticklabels([f"rank {k}" for k in range(1, places + 1)], fontsize=8)
    ax.set_yticks(range(len(products)))
    ax.set_yticklabels(products, fontsize=8)
    ax.set_title(f"Rank frequency over {result['ranking']['runs_with_ranking']} runs", fontsize=10)
    return _save(fig, path)


def _pct_bars(rows: list[tuple[str, float]], title: str, xlabel: str, path: Path) -> Path | None:
    """One series of percentages in one colour, each bar labelled with its
    value in plain ink: the number carries the reading, not the colour."""
    if not rows:
        return None
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 0.36 * len(rows) + 1.3), dpi=130)
    fig.patch.set_facecolor("white")
    vals = [v for _, v in rows]
    ax.barh(range(len(rows)), vals, height=0.62, color=ORDER_BAND)
    for y, v in enumerate(vals):
        ax.text(min(v + 1, 101), y, f"{v:.1f}", va="center", fontsize=7, color="#222222")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([k for k, _ in rows], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_title(title, fontsize=10)
    ax.grid(axis="x", color="#e6e6e6", lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return _save(fig, path)


def draw_match(res: dict[str, Any], folder: Path) -> list[Path]:
    """The RQ5 match figures, from `core.rq5_match.match()`'s result."""
    n = len(res["runs"])
    over = [(f"{o['layer']}: {o['measure']}", o["mean_pct"]) for o in res["overview"]
            if o["mean_pct"] is not None]
    compared = [f for f in res["api_facts"] if not f["volatile"] and f["match_pct"] is not None]
    # A hundred facts at 100 % is one sentence, not a hundred bars: only the
    # facts that differed are drawn, and the title says how many did not.
    facts = [(f"{f['label']}: {f['fact']}", f["match_pct"]) for f in compared
             if f["match_pct"] < 100]
    texts = [(t["unit"], t["similarity_pct"]) for t in res["llm_texts"]
             if t["similarity_pct"] is not None]
    out = [_pct_bars(over, f"RQ5 match over {n} runs: API facts and LLM interpretation",
                     "Mean Match (%)", folder / "match_overview.png"),
           _pct_bars(facts, f"API facts that differed between runs ({len(compared) - len(facts)} of "
                            f"{len(compared)} matched in every run pair, not drawn)",
                     "Match (%) Over Run Pairs", folder / "match_api_facts.png"),
           _pct_bars(texts, f"LLM interpretation: text match per text ({n} runs)",
                     "Text Match (%): Mean Character Similarity Over Run Pairs", folder / "match_llm_text.png")]
    return [p for p in out if p is not None]


def draw_all(result: dict[str, Any], folder: Path) -> list[Path]:
    out = [numeric_spread(result, folder / "numeric_spread.png"),
           chip_agreement(result, folder / "chip_agreement.png"),
           rank_frequency(result, folder / "rank_frequency.png")]
    return [p for p in out if p is not None]
