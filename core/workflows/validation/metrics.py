"""Confusion matrix and agreement metrics for two binary flood maps.

Encoding everywhere (plan rule 2): `1 = flood`, `0 = non-flood`, `255 = no-data`.

**A metric that is undefined is None, never 0.** Precision with no predicted
flood, recall with no reference flood, MCC with a constant class: each is a
division by zero, and reporting it as 0 would read as "the product is wrong"
when the truth is "there is nothing to measure". Entro reads None as exactly
that.

Formulas (TP = both flood, FP = product flood only, FN = reference flood only,
TN = both non-flood):

    overall accuracy = (TP + TN) / N
    precision        = TP / (TP + FP)          commission error = 1 - precision
    recall           = TP / (TP + FN)          omission error   = 1 - recall
    F1               = 2 TP / (2 TP + FP + FN)
    IoU              = TP / (TP + FP + FN)     (critical success index)
    MCC              = (TP TN - FP FN) / sqrt((TP+FP)(TP+FN)(TN+FP)(TN+FN))

MCC is computed with Python integers before the one square root, so a basin of
tens of millions of pixels cannot overflow.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

FLOOD, NONFLOOD, NODATA = 1, 0, 255


def confusion(product: np.ndarray, reference: np.ndarray,
              valid: np.ndarray | None = None) -> dict[str, int]:
    """TP, FP, FN, TN over the pixels where both maps have data (and `valid`).

    A pixel that is no-data in either map is left out of every count; how many
    were left out is the caller's to report as coverage.
    """
    product = np.asarray(product)
    reference = np.asarray(reference)
    if product.shape != reference.shape:
        raise ValueError(f"product {product.shape} and reference {reference.shape} "
                         "are not on the same grid")
    both = (product != NODATA) & (reference != NODATA)
    if valid is not None:
        both &= np.asarray(valid, dtype=bool)
    p = product == FLOOD
    r = reference == FLOOD
    return {
        "tp": int(np.count_nonzero(both & p & r)),
        "fp": int(np.count_nonzero(both & p & ~r)),
        "fn": int(np.count_nonzero(both & ~p & r)),
        "tn": int(np.count_nonzero(both & ~p & ~r)),
    }


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    """Every agreement metric the table reports, from one confusion matrix."""
    tp, fp, fn, tn = (int(v) for v in (tp, fp, fn, tn))
    if min(tp, fp, fn, tn) < 0:
        raise ValueError("confusion-matrix counts cannot be negative")
    n = tp + fp + fn + tn
    mcc_den = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": n,
        "overall_accuracy": _ratio(tp + tn, n),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "iou": _ratio(tp, tp + fp + fn),
        "mcc": ((tp * tn - fp * fn) / math.sqrt(mcc_den)) if mcc_den else None,
        # Share of the compared pixels the reference calls flood. Read beside
        # overall accuracy: at 2% flood, a map of "no flood anywhere" scores
        # 0.98 overall accuracy and is useless.
        "reference_flood_share": _ratio(tp + fn, n),
    }


def evaluate(product: np.ndarray, reference: np.ndarray,
             valid: np.ndarray | None = None) -> dict[str, Any]:
    """`metrics(confusion(...))` -- the one call the pipeline makes."""
    return metrics(**confusion(product, reference, valid))
