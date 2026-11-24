"""Every agreement metric under every answer that rests on a reference-map
comparison -- written in Python, after every model has finished.

User guardrail 2026-11-22. The Kelani accuracy answer reproduced precision,
recall and F1 and dropped overall accuracy, IoU and MCC: a model choosing
which columns of a result to show is a model choosing which evidence the
reader sees. So, like the sources (core/attribution.py), the full table is
appended by code whenever an `accuracy_metrics` result was produced or read
this turn. The model may discuss any metric; it cannot remove one.

Every value is COPIED from the stored result -- the tool's table rows, and
for a result stored before a metric reached those rows (overall accuracy and
the reference flood share, before 2026-11-22) the same product's own
`metrics` block. Nothing is recomputed. A metric that is undefined (a zero
denominator) prints `n/a`; one the result does not carry at all prints
`not in the stored result`, so a gap is visible, never silent.
"""

from __future__ import annotations

from typing import Any

from .workflows.validation.table import METRICS, METRIC_KEYS

NAME = "accuracy_metrics"
MISSING = "not in the stored result"


def _fmt(value: Any, key: str) -> str:
    if value is None:
        return "n/a"
    if key in ("coverage", "reference_flood_share"):
        return f"{float(value) * 100:.1f}%"
    return f"{float(value):.3f}"


def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool's rows with every metric present, copied from the result."""
    by_id = {p.get("id"): (p.get("metrics") or {}) for p in payload.get("products") or []}
    out = []
    for row in payload.get("table") or []:
        full = dict(row)
        own = by_id.get(row.get("id")) or {}
        for key in ["coverage", *METRIC_KEYS]:
            if key not in full and key in own:
                full[key] = own[key]
        out.append(full)
    return out


def render(payload: dict[str, Any]) -> str:
    """The markdown block: one row per product, every metric a column."""
    table = rows(payload)
    if not table:
        return ""
    ref = ((payload.get("reference") or {}).get("code")
           or (payload.get("reference") or {}).get("name") or "the reference map")
    head = ["Product", "Coverage", *[label for _, label in METRICS], "Metric rank", "TOPSIS rank"]
    lines = [f"**Agreement with {ref} -- every metric**", "",
             "| " + " | ".join(head) + " |", "|---|" + "---:|" * (len(head) - 1)]
    for r in table:
        if r.get("status") == "evaluated":
            cells = ([str(r.get("product", r.get("id")))]
                     + [(_fmt(r[k], k) if k in r else MISSING) for k in ["coverage", *METRIC_KEYS]]
                     + [str(r.get("metric_rank") or "n/a"), str(r.get("topsis_rank") or "n/a")])
        else:
            cells = ([str(r.get("product", r.get("id"))), f"not evaluated: {r.get('note', '')}"]
                     + ["--"] * len(METRIC_KEYS) + ["--", str(r.get("topsis_rank") or "n/a")])
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "Agreement with an operational reference map, not accuracy against ground "
              "truth. Overall accuracy is read beside the reference flood share: where little "
              "is flooded, a map of no flood scores high. Undefined ratios are n/a."]
    return "\n".join(lines)


def used(monty_result: dict | None) -> list[dict[str, Any]]:
    """Every accuracy_metrics payload this turn produced or read."""
    from . import attribution, workflows

    out = []
    for result in ((monty_result or {}).get("results") or {}).values():
        if (result.get("status") == "success"
                and workflows.canonical(str(result.get("task") or "")) == NAME
                and isinstance(result.get("output"), dict)):
            out.append(result["output"])
    for name, payload in (attribution._READ.get() or {}).items():
        if name == NAME and isinstance(payload, dict) and payload not in out:
            out.append(payload)
    return out


def append(answer: str, monty_result: dict | None) -> str:
    """The answer with the full table under it, once per comparison."""
    blocks = [b for b in (render(p) for p in used(monty_result)) if b]
    if not blocks:
        return answer
    return (answer or "").rstrip() + "\n\n" + "\n\n".join(dict.fromkeys(blocks))
