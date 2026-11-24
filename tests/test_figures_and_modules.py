"""Feedback on the Kelani basin run (user, 2026-11-19): raster styling, class
bins, local acquisition times, the Module 6/7 split with user weights, the
renamed criteria, and the Module 1 chart. No network, no model.
Runs under pytest or as a plain script.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KELANI = dict(cloud_pct=77.0, wind_ms=3.4, veg_pct=52.4, steep_pct=5.39, urban_pct=0.038)


def test_ticks_keep_clear_of_the_edges_and_share_one_format() -> None:
    from core.figures import _degree_ticks

    ticks, fmt = _degree_ticks(79.945, 80.155)
    span = 80.155 - 79.945
    assert all(79.945 + 0.06 * span <= t <= 80.155 - 0.06 * span for t in ticks), ticks
    labels = [fmt.format(t) for t in ticks]
    assert len({len(l.split(".")[1]) for l in labels}) == 1, labels   # one decimal count
    assert "80.00" in labels, labels


def test_class_bins_are_written_out() -> None:
    from core.figures import CLASS_SOURCES, _class_bins, water_bins

    bins = _class_bins()
    # Aryal et al. (2022) NDVI breaks (2026-11-19); slope back to the five
    # classes on Horn's slope (2026-11-21).
    assert bins["ndvi_class"] == {1: "NDVI < 0.19", 2: "NDVI 0.19 to 0.40", 3: "NDVI 0.40 to 0.50",
                                  4: "NDVI 0.50 to 0.60", 5: "NDVI ≥ 0.60"}
    assert bins["slope_class"] == {1: "0 to 2°", 2: "2 to 5°", 3: "5 to 15°",
                                   4: "15 to 30°", 5: "≥ 30°"}
    assert water_bins(90, 25) == {1: "occurrence > 0 to 25%", 2: "occurrence 25 to 90%",
                                  3: "occurrence ≥ 90%"}
    assert "Pekel" in CLASS_SOURCES["water_class"]
    assert "Aryal" in CLASS_SOURCES["ndvi_class"] and "Horn" in CLASS_SOURCES["slope_class"]
    assert "Ozdemir" not in CLASS_SOURCES["slope_class"]
    assert "pending" not in CLASS_SOURCES["ndvi_class"] + CLASS_SOURCES["slope_class"]


def test_the_ranking_reads_new_and_old_classes_alike() -> None:
    """Steep is slope >= 15 (steep + very_steep, restored 2026-11-21); results
    stored on 19-21 Nov read their 5-degree bands from 15 up. Dense is NDVI >= 0.60."""
    from core.workflows.mcdm import indices_from_payloads  # noqa: F401  (imports cleanly)
    from core.workflows.terrain import STEEP_BANDS_19_21_NOV, STEEP_CLASSES, STEEP_FROM_DEG

    assert STEEP_CLASSES == ("steep", "very_steep") and STEEP_FROM_DEG == 15.0
    assert STEEP_BANDS_19_21_NOV[0] == "deg_15_20"
    from core.workflows import sentinel

    assert sentinel.NDVI_BINS[-1][:2] == (0.60, 1.01) and sentinel.DENSE_CLASS == "very_high_greenness"


def test_no_approx_and_no_stretch_line(tmp: Path | None = None) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from core.figures import render

    with tempfile.TemporaryDirectory() as raw:
        tif = Path(raw) / "elevation.tif"
        with rasterio.open(tif, "w", driver="GTiff", height=40, width=80, count=1,
                           dtype="float32", crs="EPSG:4326",
                           transform=from_origin(79.945, 7.0, 0.002625, 0.003)) as ds:
            ds.write(np.linspace(0, 80, 3200, dtype="float32").reshape(40, 80), 1)
        out = render(tif, Path(raw) / "e.png", name="elevation")
        assert out["scale_bar"].endswith("km") and "approx" not in out["scale_bar"]
        assert "pixel" in out                         # in the metadata, not on the figure
        assert Path(out["png"]).is_file()


def test_every_pass_is_listed_in_local_time() -> None:
    from core import dating

    text = dating.pass_times(["2025-11-26T00:25:15Z", "2025-11-26T12:49:16Z",
                              "2025-12-02T00:24:00Z"], "Asia/Colombo")
    assert text == ("26 Nov 2025 at 05:55 and 18:19; 2 Dec 2025 at 05:54 "
                    "(local time, UTC+05:30, Asia/Colombo)"), text


def test_module_6_shows_weights_and_module_7_takes_the_users() -> None:
    from core.workflows.mcdm import mcdm_decision_matrix, rank_flood_products

    m6 = mcdm_decision_matrix(**KELANI)
    assert "ranking" not in m6 and "topsis" not in m6 and m6["method"]["ranked"] is False
    weights6 = {c["id"]: c["weight"] for c in m6["criteria"]}
    plain = rank_flood_products(**KELANI)
    assert {c["id"]: c["weight"] for c in plain["criteria"]} == weights6
    assert plain["weights_source"].startswith("computed") and not plain["weights_changed"]
    user = rank_flood_products(**KELANI, weights={"W1": 0.3})
    assert user["weights_source"] == "user-stated at Module 7"
    w1 = next(r for r in user["weights_changed"] if r["id"] == "W1")
    assert w1["stated"] == 0.3 and abs(sum(c["weight"] for c in user["criteria"]) - 1) < 1e-9
    for bad in ({"X9": 0.1}, {"W1": -1}):
        try:
            rank_flood_products(**KELANI, weights=bad)
            raise AssertionError(f"{bad} was accepted")
        except ValueError:
            pass


def test_the_closeness_meter_is_drawn_only_into_a_folder() -> None:
    from core.workflows.mcdm import rank_flood_products

    assert "figure" not in rank_flood_products(**KELANI)          # library callers
    with tempfile.TemporaryDirectory() as raw:
        out = rank_flood_products(**KELANI, output_dir=raw, event_id="e")
        assert Path(out["figure"]["path"]).is_file()


def test_the_criteria_renames() -> None:
    from core.workflows import _mcdm_data as D

    names = {c["id"]: c["name"] for c in D.CRITERIA}
    assert {"L1", "L2", "L3"} <= set(names) and not {"T1", "T2", "T3"} & set(names)
    assert names["P1"] == "Spatial resolution" and names["P4"] == "Uncertainty layer"
    assert names["P5"] == "Archive length"
    assert {c["group"] for c in D.CRITERIA if c["id"].startswith("L")} == {"Land characteristics"}


def test_module_1_offers_the_chart_not_the_count_table() -> None:
    from core import tables

    assert "data_availability" not in tables.BUILDERS
    # Module 6 is a figure now (2026-11-22), not the white table.
    assert "mcdm_decision_matrix" not in tables.BUILDERS


def test_module_6_scores_can_be_changed_and_are_checked() -> None:
    from core.workflows.mcdm import mcdm_decision_matrix, rank_flood_products

    out = mcdm_decision_matrix(**KELANI, scores={"P2": {"GFM (Sentinel-1)": 6}, "L1|dswx_hls": 1})
    assert out["decision_matrix"]["values"]["gfm"]["P2"] == 6.0
    assert {(c["criterion"], c["alternative"], c["was"]) for c in out["scores_changed"]} == {
        ("P2", "gfm", 12), ("L1", "dswx_hls", 2)}, out["scores_changed"]
    assert out["scores_source"] == "user-stated at Module 6"
    plain = rank_flood_products(**KELANI)
    assert plain["decision_matrix"]["values"]["gfm"]["P2"] == 12 and not plain["scores_changed"]
    for bad in ({"P3": {"gfm": 0.5}}, {"P4": {"gfm": 3}}, {"X1": {"gfm": 1}},
                {"P2": {"nope": 1}}, {"P2": 5}, {"P1": {"gfm": -1}}):
        try:
            mcdm_decision_matrix(**KELANI, scores=bad)
            raise AssertionError(f"{bad} was accepted")
        except ValueError:
            pass


def test_module_7_ranks_with_the_changes_made_at_module_6() -> None:
    from core import experiment
    from core.workflows.mcdm import mcdm_decision_matrix, rank_flood_products

    class Store:
        def __init__(self, payload):
            self.payload = payload

        def cached_input(self, name):
            return {"payload": self.payload} if name == "mcdm_decision_matrix" else None

    m6 = mcdm_decision_matrix(**KELANI, weights={"L1": 0.2}, scores={"P2": {"gfm": 6}})
    experiment.set_current(Store(m6))
    try:
        ranked = rank_flood_products(**KELANI)
        library = rank_flood_products(**KELANI, use_module6=False)
        own = rank_flood_products(**KELANI, weights={"W1": 0.3})
    finally:
        experiment.set_current(None)
    assert ranked["weights_source"] == "user-stated at Module 6"
    assert ranked["decision_matrix"]["values"]["gfm"]["P2"] == 6.0
    assert {c["id"]: c["weight"] for c in ranked["criteria"]} == \
        {c["id"]: c["weight"] for c in m6["criteria"]}
    assert library["weights_source"].startswith("computed") and not library["scores_changed"]
    assert own["weights_source"] == "user-stated at Module 7" and not own["scores_changed"]


def test_module_6_draws_its_figure_and_reading_lines() -> None:
    from core import result_view
    from core.workflows.mcdm import mcdm_decision_matrix

    with tempfile.TemporaryDirectory() as raw:
        out = mcdm_decision_matrix(**KELANI, output_dir=raw, event_id="e")
        assert Path(out["figure"]["path"]).is_file(), out.get("figure_problem")
    assert "figure" not in mcdm_decision_matrix(**KELANI)          # library callers
    guide = " ".join(out["reading_guide"])
    assert "Benefit criteria (P3, P4, P5, P6)" in guide and "lower score is better" in guide
    assert "raises L1 +52%" in guide and "0.070 each" in guide, guide
    view = result_view.view("mcdm_decision_matrix", out)
    assert "never retype the matrix" in view and "<table" not in view


def test_event_criteria_are_weighted_first() -> None:
    """User decision 2026-11-22: each event criterion is 1/12 raised by its
    index (52% dense vegetation -> 1/12 x 1.52); W3 stays at 1/12; what is
    left is shared equally by P1-P6."""
    from core.workflows.mcdm import modulators, rank_flood_products

    out = rank_flood_products(**KELANI)
    w = {c["id"]: c["weight"] for c in out["criteria"]}
    m = modulators(**KELANI)
    base = 1 / 12
    assert abs(w["L1"] - base * 1.524) < 1e-12 and abs(w["W3"] - base) < 1e-12
    assert abs(w["W1"] - base * (1 + m["W1"])) < 1e-12 and abs(w["W2"] - base) < 1e-12
    left = 1 - sum(w[c] for c in ("W1", "W2", "W3", "L1", "L2", "L3"))
    assert all(abs(w[f"P{i}"] - left / 6) < 1e-12 for i in range(1, 7))
    assert abs(sum(w.values()) - 1) < 1e-12 and min(w.values()) > 0
    assert out["method"]["weighting"] == "event_first"
    # The specification workbook's rule is still there, by name.
    old = rank_flood_products(**KELANI, weighting="multiplicative")
    assert abs({c["id"]: c["weight"] for c in old["criteria"]}["W2"]) < 1e-12


def test_module_6_sources_list_the_evidence_and_the_inputs() -> None:
    from core import attribution
    from core.workflows.mcdm import mcdm_decision_matrix

    out = mcdm_decision_matrix(**KELANI)
    text = attribution.render([("mcdm_decision_matrix", out)], "", "Asia/Colombo")
    assert "Wagner et al. (2026)" in text and "[S1]" in text, text
    assert "acquisition time not recorded" not in text and "S1; S2" not in text, text
    assert "no acquisition of its own" in text
    plain = attribution.render([("mcdm_decision_matrix", out)], attribution.PLAIN, "Asia/Colombo")
    assert "Documents behind the scores" in plain and "[S1]" not in plain


def test_the_thesis_path_has_modules_6_and_7_apart() -> None:
    from core import thesis_path

    steps = {s["id"]: s for s in thesis_path.view()}
    assert steps["matrix"]["output"] == "Module 6" and steps["ranking"]["output"] == "Module 7"
    assert steps["ranking"]["needs"] == ["matrix"]


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
