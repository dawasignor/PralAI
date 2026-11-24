"""Point labels and Stehman estimators for the labelled sample.

The cumulative rule on hand-made observations; the estimators exact on a
census and unbiased, with honest standard errors, over many simulated samples.
No network, no model. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workflows.validation import estimate as est  # noqa: E402

PEAK = date(2025, 11, 28)
IST = timedelta(hours=5, minutes=30)


def _obs(day: str, cls: str, source: str = "sentinel2_l2a", reason: str = "") -> dict:
    return {"image_id": f"{source}-{day}", "image_datetime_utc": f"2025-{day}T05:17:00Z",
            "class": cls, "source": source, "unobservable_reason": reason}


def test_flood_anywhere_in_the_window_wins() -> None:
    lab = est.point_label([_obs("11-25", "flood"), _obs("11-30", "not_flood")], PEAK, IST)
    assert lab["label"] == "flood", lab


def test_dry_needs_a_clear_view_on_or_after_the_peak() -> None:
    assert est.point_label([_obs("11-30", "not_flood")], PEAK, IST)["label"] == "not_flood"
    before = est.point_label([_obs("11-25", "not_flood"), _obs("11-30", "unobservable", reason="cloud")],
                             PEAK, IST)
    assert before["label"] == "unresolved" and "before the peak" in before["reason"], before
    cloud = est.point_label([_obs("11-25", "unobservable", reason="cloud"),
                             _obs("11-30", "unobservable", reason="cloud")], PEAK, IST)
    assert cloud["label"] == "unresolved" and "cloud" in cloud["reason"], cloud


def test_preexisting_water_is_its_own_clear_class() -> None:
    lab = est.point_label([_obs("11-30", "preexisting_water")], PEAK, IST)
    assert lab["label"] == "preexisting_water", lab


def test_the_peak_day_is_a_local_day() -> None:
    # 20:00 UTC on 27 Nov is 01:30 on 28 Nov in Sri Lanka: on the peak day.
    o = {"image_id": "x", "image_datetime_utc": "2025-11-27T20:00:00Z", "class": "not_flood",
         "source": "sentinel2_l2a"}
    assert est.point_label([o], PEAK, IST)["label"] == "not_flood"
    assert est.point_label([o], PEAK, timedelta(0))["label"] == "unresolved"


def test_a_product_is_not_scored_on_its_own_sensor() -> None:
    obs = [_obs("11-30", "flood"), _obs("11-27", "not_flood", source="landsat_c2_l2")]
    hls = est.independent_of(obs, ["sentinel-2", "landsat"])
    assert hls == [] and est.point_label(hls, PEAK, IST)["label"] == "unresolved"
    gfm = est.independent_of(obs, ["sentinel-1"])
    assert len(gfm) == 2


def _population(seed: int = 1):
    """Three strata of indicator values (r, m, c) with different flood shares."""
    rng = np.random.default_rng(seed)
    pop = {}
    for h, (N, pr, pm, pc) in {"A": (400, 0.8, 0.9, 1.0), "B": (900, 0.2, 0.1, 0.8),
                               "C": (3000, 0.02, 0.01, 0.95)}.items():
        r = (rng.random(N) < pr).astype(int)
        m = np.where(rng.random(N) < 0.85, r, (rng.random(N) < pm).astype(int))
        c = (rng.random(N) < pc).astype(int)
        pop[h] = np.stack([r, m, c], axis=1)
    return pop


def _truth(pop, name):
    num, den = est.RATIOS[name]
    a = np.concatenate(list(pop.values()))
    return num(a[:, 0], a[:, 1], a[:, 2]).sum() / den(a[:, 0], a[:, 1], a[:, 2]).sum()


def test_a_census_is_exact_with_zero_variance() -> None:
    pop = _population()
    strata = {h: {"N": len(v)} for h, v in pop.items()}
    acc = est.accuracy(strata, {h: [tuple(x) for x in v] for h, v in pop.items()})
    for name in est.RATIOS:
        assert abs(acc[name]["estimate"] - _truth(pop, name)) < 1e-12, name
        assert acc[name]["se"] < 1e-9, (name, acc[name]["se"])
    tot = est.total(strata, {h: v[:, 0] for h, v in pop.items()})
    assert tot["estimate"] == sum(int(v[:, 0].sum()) for v in pop.values()) and tot["se"] < 1e-9


def test_estimators_are_unbiased_and_their_se_is_honest() -> None:
    pop = _population()
    strata = {h: {"N": len(v)} for h, v in pop.items()}
    # Main-sample sizes. At pilot sizes (about 10 per stratum) the linearised
    # ratio SE runs 5-20% low when flood is rare -- known, and the pilot report
    # says its intervals are optimistic for that reason.
    n = {"A": 150, "B": 150, "C": 300}
    rng = np.random.default_rng(2)
    est_r, se_r, est_t, se_t = [], [], [], []
    for _ in range(3000):
        pts = {h: [tuple(x) for x in pop[h][rng.choice(len(pop[h]), n[h], replace=False)]] for h in pop}
        acc = est.accuracy(strata, pts)
        est_r.append(acc["recall"]["estimate"])
        se_r.append(acc["recall"]["se"])
        t = est.total(strata, {h: np.array(v)[:, 0] for h, v in pts.items()})
        est_t.append(t["estimate"])
        se_t.append(t["se"])
    truth_r = _truth(pop, "recall")
    truth_t = sum(int(v[:, 0].sum()) for v in pop.values())
    # The total is exactly unbiased; the ratio nearly so (bias O(1/n)).
    assert abs(np.mean(est_t) - truth_t) < 3 * np.std(est_t) / np.sqrt(len(est_t)), (np.mean(est_t), truth_t)
    assert abs(np.mean(est_r) - truth_r) < 0.01, (np.mean(est_r), truth_r)
    # The mean estimated SE matches the spread of the estimates within 10%.
    assert abs(np.mean(se_t) / np.std(est_t) - 1) < 0.10, (np.mean(se_t), np.std(est_t))
    assert abs(np.mean(se_r) / np.std(est_r) - 1) < 0.10, (np.mean(se_r), np.std(est_r))


def test_an_empty_or_thin_stratum_is_reported_not_filled() -> None:
    strata = {"A": {"N": 100}, "B": {"N": 100}}
    t = est.total(strata, {"A": [1.0, 0.0]})
    assert t["estimate"] is None and "B" in t["undefined"], t
    one = est.total(strata, {"A": [1.0, 0.0], "B": [1.0]})
    assert one["estimate"] is not None and one["se"] is None, one
    r = est.ratio(strata, {"A": [0, 0], "B": [0, 0]}, {"A": [0, 0], "B": [0, 0]})
    assert r["estimate"] is None and "denominator" in r["undefined"], r


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
