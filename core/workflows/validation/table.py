"""Step 5: the one table -- one row per product, sorted by F1.

    | Product | Coverage | Overall accuracy | Precision | Recall | F1 | IoU | MCC |
    | Reference flood share | Metric rank | TOPSIS rank |

**Every metric, every time** (user guardrail 2026-11-22: a Kelani answer
showed precision, recall and F1 and dropped overall accuracy, IoU and MCC).
`METRICS` is the one list of what `metrics.metrics` computes; the table, the
CSV, the Markdown and the table written under every answer
(`core/agreement_table.py`) are all built from it, and `check_complete`
refuses a table in which an evaluated product lacks one.

Metric rank is by F1, highest first; equal F1 share a rank. A product that
could not be evaluated keeps its row, at the bottom, with the reason in place
of numbers -- dropping it would let the metric order look complete when it
covers fewer products than the TOPSIS order it is set beside.

A one-line note under the table states the reference, temporal mode, grid mode
and pixel counts (plan step 5). Saved as CSV and Markdown.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

# Every agreement metric, in the order shown (key, column). Overall accuracy
# is always read beside the reference flood share: at 2% flood a map of "no
# flood anywhere" scores 0.98.
METRICS: list[tuple[str, str]] = [
    ("overall_accuracy", "Overall accuracy"), ("precision", "Precision"),
    ("recall", "Recall"), ("f1", "F1"), ("iou", "IoU"), ("mcc", "MCC"),
    ("reference_flood_share", "Reference flood share"),
]
METRIC_KEYS = [k for k, _ in METRICS]
COLUMNS = (["Product", "Coverage"] + [label for _, label in METRICS]
           + ["Metric rank", "TOPSIS rank"])


class IncompleteMetrics(ValueError):
    """An evaluated product is missing an agreement metric."""


def check_complete(rows: list[dict[str, Any]]) -> None:
    """The guardrail: every evaluated row carries every metric key (a value,
    or None for an undefined ratio -- never a missing key)."""
    for row in rows:
        if row.get("status") != "evaluated":
            continue
        gone = [k for k in ["coverage"] + METRIC_KEYS if k not in row]
        if gone:
            raise IncompleteMetrics(
                f"{row.get('product', row.get('id'))} is missing {', '.join(gone)}; every "
                "comparison with a reference map reports every metric.")


def topsis_ranks(cfg: dict[str, Any]) -> dict[str, int]:
    """Product id -> TOPSIS rank, from the cached ranking payload or a CSV."""
    from .config import resolve

    path = resolve(cfg, (cfg.get("topsis") or {}).get("path"))
    if path is None or not path.is_file():
        return {}
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8") as fh:
            return {row["product"]: int(row["rank"]) for row in csv.DictReader(fh)}
    data = json.loads(path.read_text(encoding="utf-8"))
    payload = data.get("payload", data)
    return {row["id"]: int(row["rank"]) for row in payload.get("ranking") or []}


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def build(results: list[dict[str, Any]], topsis: dict[str, int]) -> list[dict[str, Any]]:
    evaluated = [r for r in results if r.get("metrics")]
    evaluated.sort(key=lambda r: (r["metrics"]["f1"] is None, -(r["metrics"]["f1"] or 0.0)))
    rows, rank, previous = [], 0, object()
    for position, r in enumerate(evaluated, start=1):
        f1 = r["metrics"]["f1"]
        if f1 != previous:
            rank, previous = position, f1
        m = r["metrics"]
        rows.append({"id": r["id"], "product": r["label"], "coverage": m["coverage"],
                     **{k: m[k] for k in METRIC_KEYS},
                     "metric_rank": rank if f1 is not None else None,
                     "topsis_rank": topsis.get(r["id"]), "status": "evaluated",
                     "note": ""})
    for r in results:
        if not r.get("metrics"):
            rows.append({"id": r["id"], "product": r["label"], "coverage": None,
                         **{k: None for k in METRIC_KEYS}, "metric_rank": None,
                         "topsis_rank": topsis.get(r["id"]), "status": "not evaluated",
                         "note": r.get("reason", "")})
    check_complete(rows)
    return rows


def note_line(cfg: dict[str, Any], results: list[dict[str, Any]]) -> str:
    temporal = cfg["temporal"]
    when = (f"cumulative {temporal['window'][0]} to {temporal['window'][1]}"
            if temporal["mode"] == "cumulative"
            else f"single date {temporal['date']} +/- {temporal.get('tolerance_days', 1)} d")
    counts = "; ".join(f"{r['label']} {r['metrics']['compared_pixels']:,} px "
                       f"({r['metrics']['pixel_size_m'][0]:g} m)"
                       for r in results if r.get("metrics"))
    # The reference by its own code, never a fixed publisher: this line said
    # "UNOSAT" for every reference until EMSR851 (2026-11-18).
    return (f"Agreement with the reference {cfg['reference']['code']} "
            f"(not accuracy against ground truth); temporal mode: {when}; grid mode: "
            f"native (reference aggregated to each product grid, flood fraction > "
            f"{cfg['evaluation']['flood_fraction_threshold']}); pixels compared: "
            f"{counts or 'none'}.")


def write(rows: list[dict[str, Any]], note: str, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path, md_path = out_dir / "agreement_table.csv", out_dir / "agreement_table.md"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS + ["Status", "Note"])
        for r in rows:
            writer.writerow([r["product"]] + [
                "" if r[k] is None else r[k] for k in
                ["coverage", *METRIC_KEYS, "metric_rank", "topsis_rank"]]
                + [r["status"], r["note"]])
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        if r["status"] == "evaluated":
            cells = ([r["product"], _fmt(r["coverage"])]
                     + [_fmt(r[k]) for k in METRIC_KEYS]
                     + [str(r["metric_rank"] or "n/a"), str(r["topsis_rank"] or "n/a")])
        else:
            cells = ([r["product"], f"not evaluated: {r['note']}"]
                     + [""] * len(METRIC_KEYS) + ["", str(r["topsis_rank"] or "n/a")])
        lines.append("| " + " | ".join(cells) + " |")
    md_path.write_text("\n".join(lines) + f"\n\n{note}\n", encoding="utf-8")
    return {"csv": str(csv_path), "markdown": str(md_path)}
