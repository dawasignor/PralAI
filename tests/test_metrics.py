"""Agreement metrics, checked against hand-computed matrices and sklearn.

    pytest tests/test_metrics.py
    python tests/test_metrics.py

Validation plan, "Metrics": hand-computed toy confusion matrices, a cross-check
against `sklearn.metrics`, and the edge cases -- no positives, all no-data,
perfect agreement. sklearn is a dev-only dependency (requirements-dev.txt); the
cross-check is skipped, and says so, when it is not installed.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.workflows.validation.metrics import (  # noqa: E402
    NODATA, confusion, evaluate, metrics)


def close(a, b, tol=1e-12) -> bool:
    return (a is None and b is None) or (a is not None and b is not None
                                         and abs(a - b) <= tol)


def test_hand_computed() -> None:
    # TP=40 FP=10 FN=20 TN=930, worked by hand.
    m = metrics(40, 10, 20, 930)
    assert m["n"] == 1000
    assert close(m["overall_accuracy"], 970 / 1000)
    assert close(m["precision"], 40 / 50)
    assert close(m["recall"], 40 / 60)
    assert close(m["f1"], 80 / 110)
    assert close(m["iou"], 40 / 70)
    assert close(m["mcc"], (40 * 930 - 10 * 20) / math.sqrt(50 * 60 * 940 * 950))
    assert close(m["reference_flood_share"], 60 / 1000)


def test_second_hand_matrix() -> None:
    # TP=5 FP=5 FN=5 TN=5: no better than chance, so MCC is exactly 0.
    m = metrics(5, 5, 5, 5)
    assert close(m["f1"], 0.5) and close(m["iou"], 1 / 3) and close(m["mcc"], 0.0)


def test_confusion_skips_nodata() -> None:
    product = np.array([1, 1, 0, 0, NODATA, 1])
    reference = np.array([1, 0, 1, 0, 1, NODATA])
    assert confusion(product, reference) == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}


def test_valid_mask_restricts() -> None:
    product = np.array([1, 1, 0, 0])
    reference = np.array([1, 1, 0, 0])
    out = confusion(product, reference, valid=np.array([True, False, True, False]))
    assert out == {"tp": 1, "fp": 0, "fn": 0, "tn": 1}


def test_no_positives() -> None:
    # Neither map has any flood: precision, recall, F1, IoU and MCC are all
    # undefined -- None, never 0 -- while agreement is perfect.
    m = evaluate(np.zeros(10, int), np.zeros(10, int))
    assert m["tn"] == 10 and m["overall_accuracy"] == 1.0
    assert all(m[k] is None for k in ("precision", "recall", "f1", "iou", "mcc"))


def test_product_misses_everything() -> None:
    # Reference has flood, product has none: recall 0, precision undefined.
    m = metrics(0, 0, 7, 93)
    assert m["recall"] == 0.0 and m["precision"] is None
    assert m["f1"] == 0.0 and m["iou"] == 0.0 and m["mcc"] is None


def test_all_nodata() -> None:
    m = evaluate(np.full(8, NODATA), np.full(8, NODATA))
    assert m["n"] == 0
    assert all(m[k] is None for k in ("overall_accuracy", "precision", "recall",
                                      "f1", "iou", "mcc"))


def test_perfect_agreement() -> None:
    a = np.array([1, 0, 1, 0, 0, 1])
    m = evaluate(a, a.copy())
    assert all(m[k] == 1.0 for k in ("overall_accuracy", "precision", "recall",
                                     "f1", "iou", "mcc"))


def test_large_counts_do_not_overflow() -> None:
    n = 40_000_000
    m = metrics(n, n // 3, n // 5, 5 * n)
    assert m["mcc"] is not None and -1.0 <= m["mcc"] <= 1.0


def test_shape_mismatch_refused() -> None:
    try:
        confusion(np.zeros(3), np.zeros(4))
    except ValueError:
        return
    raise AssertionError("a shape mismatch was not refused")


def test_against_sklearn() -> None:
    try:
        from sklearn.metrics import (accuracy_score, f1_score, jaccard_score,
                                     matthews_corrcoef, precision_score,
                                     recall_score)
    except ImportError:
        print("  SKIP sklearn cross-check: scikit-learn not installed "
              "(pip install -r requirements-dev.txt)")
        return
    rng = np.random.default_rng(20251128)                 # fixed seed (plan rule 4)
    for _ in range(25):
        size = int(rng.integers(50, 5000))
        ref = (rng.random(size) < rng.uniform(0.02, 0.6)).astype(int)
        flip = rng.random(size) < rng.uniform(0.0, 0.4)
        prod = np.where(flip, 1 - ref, ref)
        m = evaluate(prod, ref)
        assert close(m["overall_accuracy"], accuracy_score(ref, prod), 1e-12)
        assert close(m["precision"], precision_score(ref, prod, zero_division=0), 1e-12) \
            or m["precision"] is None
        assert close(m["recall"], recall_score(ref, prod), 1e-12)
        assert close(m["f1"], f1_score(ref, prod), 1e-12)
        assert close(m["iou"], jaccard_score(ref, prod), 1e-12)
        assert close(m["mcc"], matthews_corrcoef(ref, prod), 1e-9)


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {test.__name__} {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
