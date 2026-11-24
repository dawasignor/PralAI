"""Every agreement metric on every reference-map comparison (user guardrail
2026-11-22). No model, no network. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import agreement_table  # noqa: E402
from core.workflows.validation import table  # noqa: E402
from core.workflows.validation.metrics import metrics  # noqa: E402

SHOWN = {"overall_accuracy", "precision", "recall", "f1", "iou", "mcc",
         "reference_flood_share"}


def result(pid: str, label: str, **cm) -> dict:
    m = metrics(**cm)
    return {"id": pid, "label": label, "metrics": {**m, "coverage": 0.8, "compared_pixels": m["n"],
                                                   "pixel_size_m": [20.0, 20.0]}}


def payload() -> dict:
    results = [result("gfm", "GFM", tp=50, fp=10, fn=40, tn=900),
               result("mcdwd", "MODIS", tp=5, fp=1, fn=85, tn=909),
               {"id": "vcdwd", "label": "VIIRS", "reason": "no file in the window"}]
    rows = table.build(results, {"gfm": 1, "mcdwd": 2, "vcdwd": 3})
    return {"reference": {"code": "EMSR851_AOI02_DEL"}, "table": rows,
            "products": [{"id": r["id"], "metrics": r.get("metrics")} for r in results]}


def test_the_table_lists_every_metric_the_metrics_compute() -> None:
    computed = set(metrics(tp=1, fp=1, fn=1, tn=1)) - {"tp", "fp", "fn", "tn", "n"}
    assert set(table.METRIC_KEYS) == computed == SHOWN, (table.METRIC_KEYS, computed)


def test_every_evaluated_row_carries_every_metric() -> None:
    rows = payload()["table"]
    for row in rows:
        assert set(table.METRIC_KEYS) <= set(row), row
    assert rows[-1]["status"] == "not evaluated" and rows[-1]["overall_accuracy"] is None


def test_an_incomplete_row_is_refused() -> None:
    bad = [{"id": "gfm", "product": "GFM", "status": "evaluated", "coverage": 0.5,
            "precision": 0.9, "recall": 0.3, "f1": 0.45}]
    try:
        table.check_complete(bad)
        raise AssertionError("a row without IoU, MCC and overall accuracy was accepted")
    except table.IncompleteMetrics as exc:
        assert "overall_accuracy" in str(exc) and "iou" in str(exc) and "mcc" in str(exc)


def test_files_carry_every_metric(tmp: Path | None = None) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        out = table.write(payload()["table"], "note", Path(raw))
        md = Path(out["markdown"]).read_text(encoding="utf-8")
        head = Path(out["csv"]).read_text(encoding="utf-8").splitlines()[0]
    for label in ("Overall accuracy", "IoU", "MCC", "Reference flood share"):
        assert label in md and label in head, (label, md[:200])


def test_the_answer_gets_the_full_table_whatever_the_model_wrote() -> None:
    p = payload()
    mr = {"results": {"result_1": {"task": "accuracy_metrics", "status": "success", "output": p}}}
    answer = agreement_table.append("| Product | Precision | Recall | F1 |\n| GFM | 0.83 |", mr)
    tail = answer.split("every metric**", 1)[1]
    for label in ("Overall accuracy", "Precision", "Recall", "F1", "IoU", "MCC",
                  "Reference flood share", "Metric rank", "TOPSIS rank"):
        assert label in tail, label
    gfm = next(line for line in tail.splitlines() if line.startswith("| GFM"))
    assert gfm.count("|") == 12 and "n/a" not in gfm, gfm      # 11 columns, all filled
    assert "not evaluated: no file in the window" in tail
    assert agreement_table.append("plain", {"results": {}}) == "plain"


def test_a_result_stored_before_the_fix_is_filled_from_its_own_metrics() -> None:
    p = payload()
    for row in p["table"]:
        row.pop("overall_accuracy", None)
        row.pop("reference_flood_share", None)
    text = agreement_table.render(p)
    gfm = next(line for line in text.splitlines() if line.startswith("| GFM"))
    assert agreement_table.MISSING not in gfm and "0.950" in gfm, gfm   # (50+900)/1000


def test_kelani_where_the_session_exists() -> None:
    path = (Path(__file__).resolve().parents[1] / "sessions" / "kelani-basin-2025"
            / "inputs" / "accuracy_metrics.json")
    if not path.is_file():
        return
    text = agreement_table.render(json.loads(path.read_text(encoding="utf-8"))["payload"])
    assert agreement_table.MISSING not in text and "Overall accuracy" in text
    assert "| OPERA DSWx-HLS | 69.9% | 0.863 | 0.443 | 0.821 | 0.576 | 0.404 | 0.536 |" in text, text


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__} {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
