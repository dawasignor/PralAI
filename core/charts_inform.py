"""INFORM figures for Modules 9 and 10: four national meters and the trend.

Drawn from the stored `fetch_inform_risk` payload, nothing recomputed (as
core/charts.py): a figure cannot disagree with the line printed under it.

    inform_meters.png   INFORM risk, Vulnerability, Lack of coping capacity,
                        Climate change risk (2050) -- semicircle meters, 0-10
    inform_trend.png    one line chart: Vulnerability, Lack of coping capacity
                        and Natural hazard (all natural hazards), year by year

Every INFORM score reads the same way -- higher is worse -- and each figure
says so at its foot. The meters use one hue (the decision outputs' blue): a
score is a magnitude, never a status, and INFORM's API serves no risk class.
The trend's three lines take the first three validated categorical hues.
"""

from __future__ import annotations

import math
import textwrap
from pathlib import Path
from typing import Any

from .charts import ORDER_BAND

TRACK = "#e6e6e6"
INK, MUTED = "#222222", "#555555"
HIGHER = ("0 = lowest, 10 = highest. Higher is worse on every meter: more risk, more "
          "vulnerability, less capacity to cope, more risk under climate change.")


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _foot(fig, lines: list[str]) -> None:
    fig.text(0.01, -0.01, "\n".join(textwrap.fill(x, 150) for x in lines if x),
             fontsize=9, color="#333333", va="top")


def _save(fig, png: Path) -> Path:
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, facecolor="white", bbox_inches="tight")
    _plt().close(fig)
    return png


def meter_values(inf: dict[str, Any]) -> list[dict[str, Any]]:
    """The four meters, in order, each with its value or None (NOT KNOWN)."""
    s = inf.get("scores") or {}
    cc = (inf.get("climate_change") or {})
    ri = cc.get("risk_index") or {}
    rel = (inf.get("release") or {}).get("name", "INFORM")
    return [
        {"key": "inform_risk", "title": "INFORM risk (overall)", "value": (s.get("inform_risk") or {}).get("value"),
         "note": rel},
        {"key": "vulnerability", "title": "Vulnerability", "value": (s.get("vulnerability") or {}).get("value"),
         "note": rel},
        {"key": "lack_of_coping_capacity", "title": "Lack of coping capacity",
         "value": (s.get("lack_of_coping_capacity") or {}).get("value"), "note": rel},
        {"key": "climate_change_2050", "title": "Climate change risk, 2050",
         "value": ri.get("2050_pessimistic") if cc.get("status") == "ok" else None,
         "note": (f"pessimistic scenario (optimistic {ri.get('2050_optimistic')}; "
                  f"2022: {ri.get('baseline')})"
                  if cc.get("status") == "ok" else "INFORM Climate Change 2022: not known")},
    ]


def meters(inf: dict[str, Any], png: Path, source_line: str = "") -> Path:
    from matplotlib.patches import Wedge

    plt = _plt()
    items = meter_values(inf)
    fig, axes = plt.subplots(1, len(items), figsize=(12.5, 3.3), dpi=130)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.86, bottom=0.02, wspace=0.08)
    fig.patch.set_facecolor("white")
    for ax, m in zip(axes, items):
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-0.42, 1.12)
        ax.set_aspect("equal")
        ax.axis("off")
        # The track, then the score: 180 degrees = 0, 0 degrees = 10.
        ax.add_patch(Wedge((0, 0), 1.0, 0, 180, width=0.26, facecolor=TRACK, edgecolor="none"))
        v = m["value"]
        if v is not None:
            ax.add_patch(Wedge((0, 0), 1.0, 180 - 18 * float(v), 180, width=0.26,
                               facecolor=ORDER_BAND, edgecolor="white", linewidth=1.5))
            # A thin needle at the score, so the value reads from the arc too.
            a = math.radians(180 - 18 * float(v))
            ax.plot([0, 0.66 * math.cos(a)], [0, 0.66 * math.sin(a)], color=INK, lw=1.6,
                    solid_capstyle="round")
            ax.add_patch(plt.Circle((0, 0), 0.045, color=INK))
            ax.text(0, -0.2, f"{float(v):.1f}", ha="center", va="center", fontsize=22,
                    color=INK, fontweight="bold")
        else:
            ax.text(0, 0.18, "not known", ha="center", va="center", fontsize=12, color=MUTED)
        for tick, x in ((0, -0.87), (10, 0.87)):
            ax.text(x, -0.1, str(tick), ha="center", fontsize=10, color=MUTED)
        ax.text(0, 1.1, m["title"], ha="center", va="bottom", fontsize=12, fontweight="bold", color=INK)
        ax.text(0, -0.4, textwrap.fill(m["note"], 34), ha="center", va="top", fontsize=9,
                color=MUTED)
    country = inf.get("country", "")
    # Bigger, and national-level said in the title itself (user, 2026-11-19).
    fig.suptitle(f"{country}: NATIONAL-LEVEL INFORM scores out of 10 -- they describe "
                 "the whole country, not the area of interest", fontsize=13,
                 fontweight="bold", y=1.0)
    _foot(fig, [HIGHER, "National scores: they describe the country, not the area of "
                        "interest.", source_line])
    return _save(fig, png)


def trend_rows(inf: dict[str, Any]) -> list[dict[str, Any]]:
    return list((inf.get("trend") or {}).get("years") or [])


TREND_PANELS = (("vulnerability", "Vulnerability"),
                ("lack_of_coping_capacity", "Lack of coping capacity"),
                ("natural_hazard", "Natural hazard (all natural hazards)"))


# One line per score, validated categorical order (dataviz reference palette:
# blue, orange, aqua; validate_palette.js PASS on white, aqua < 3:1 contrast,
# so every line is also direct-labelled at its end and has its own marker).
TREND_COLOURS = ("#2a78d6", "#eb6834", "#1baf7a")
TREND_MARKERS = ("o", "s", "^")


def trend(inf: dict[str, Any], png: Path, source_line: str = "") -> Path | None:
    """One line chart: Vulnerability, Lack of coping capacity and Natural
    hazard (all natural hazards), year by year, 0-10. A year the API did not
    answer breaks the line there (a gap, never joined across or filled)."""
    years = trend_rows(inf)
    if not years:
        return None
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9.5, 3.6), dpi=130)
    fig.patch.set_facecolor("white")
    xs = [y["year"] for y in years]
    for (key, title), colour, marker in zip(TREND_PANELS, TREND_COLOURS, TREND_MARKERS):
        vals = [float(y[key]) if y.get("status") == "ok" and y.get(key) is not None else float("nan")
                for y in years]
        ax.plot(xs, vals, color=colour, lw=2, marker=marker, markersize=6.5,
                markeredgecolor="white", markeredgewidth=1.2, label=title, zorder=3)
        known = [(x, v) for x, v in zip(xs, vals) if v == v]
        if known:
            x_end, v_end = known[-1]
            x_0, v_0 = known[0]
            ax.annotate(f"{v_end:.1f}", (x_end, v_end), xytext=(7, 0), textcoords="offset points",
                        va="center", fontsize=10, color=INK)
            ax.annotate(f"{v_0:.1f}", (x_0, v_0), xytext=(-7, 0), textcoords="offset points",
                        va="center", ha="right", fontsize=10, color=INK)
    for y in years:
        if y.get("status") != "ok":
            ax.text(y["year"], 0.4, "no data", ha="center", fontsize=9, color=MUTED, rotation=90)
    ax.set_ylim(0, 10)
    ax.set_yticks([0, 2, 4, 6, 8, 10])
    ax.set_xlim(xs[0] - 0.7, xs[-1] + 0.7)
    ax.set_xticks(xs)
    ax.tick_params(labelsize=7.5, colors=MUTED)
    ax.set_ylabel("Score out of 10", fontsize=11, color=MUTED)
    ax.grid(axis="y", color=TRACK, lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, frameon=False, fontsize=10)
    t = inf.get("trend") or {}
    ax.set_title(f"{inf.get('country', '')}, NATIONAL level: INFORM trend, {xs[0]} to {xs[-1]} "
                 f"({t.get('series', 'INFORM')} series)", fontsize=12, fontweight="bold")
    fig.tight_layout()
    _foot(fig, ["0 = lowest, 10 = highest; higher is worse on all three lines. Natural hazard covers "
                "all natural hazards together (INFORM's natural-hazard component), not floods alone.",
                f"Every year re-computed with the {t.get('series', 'same')} method, so the years "
                "compare; the meters above come from a later release and may differ.", source_line])
    return _save(fig, png)
