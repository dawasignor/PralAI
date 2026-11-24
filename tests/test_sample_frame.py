"""The labelled sample's frame: dilation, strata, allocation, draw, freeze.

No network, no model. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workflows.validation import sample  # noqa: E402
from core.workflows.validation.metrics import FLOOD, NODATA, NONFLOOD  # noqa: E402


def _brute_disk(mask: np.ndarray, r: int) -> np.ndarray:
    out = np.zeros_like(mask)
    h, w = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        for yy in range(max(0, y - r), min(h, y + r + 1)):
            for xx in range(max(0, x - r), min(w, x + r + 1)):
                if (yy - y) ** 2 + (xx - x) ** 2 <= r * r:
                    out[yy, xx] = True
    return out


def test_disk_dilate_matches_brute_force() -> None:
    rng = np.random.default_rng(1)
    mask = rng.random((40, 55)) > 0.985
    for r in (0, 1, 3, 7):
        assert np.array_equal(sample.disk_dilate(mask, r), _brute_disk(mask, r)), r


def test_disk_dilate_edges_and_empty() -> None:
    mask = np.zeros((9, 9), dtype=bool)
    assert not sample.disk_dilate(mask, 3).any()
    mask[0, 0] = True
    grown = sample.disk_dilate(mask, 2)
    assert grown[2, 0] and grown[0, 2] and grown[1, 1] and not grown[2, 2]


def test_stratify_assigns_each_rule() -> None:
    domain = np.ones((1, 8), dtype=bool)
    domain[0, 7] = False
    ref = np.array([[NONFLOOD, FLOOD, NONFLOOD, NONFLOOD, NONFLOOD, NONFLOOD, NONFLOOD, NODATA]], "uint8")
    prod = np.array([[FLOOD, NONFLOOD, NONFLOOD, NODATA, NONFLOOD, NODATA, NONFLOOD, NODATA]], "uint8")
    ever = np.zeros((1, 8), dtype=bool)
    ever[0, 3] = True
    code = sample.stratify(domain, ref, {"p": prod}, ever, buffer_px=1)
    c = sample.CODES
    assert code[0, 0] == c["S1"]                     # product flood
    assert code[0, 1] == c["S2.obs"]                 # reference-only flood, observed
    assert code[0, 2] == c["S3.obs"]                 # within 1 cell of S1/S2
    assert code[0, 3] == c["S3.unobs"]               # JRC ever-wet, unobserved
    assert code[0, 4] == c["S4.obs"]
    assert code[0, 5] == c["S4.unobs"]
    assert code[0, 7] == 0                           # outside the domain


def test_allocate_splits_and_moves_with_notes() -> None:
    counts = {"S1": 500, "S2.obs": 1000, "S2.unobs": 0, "S3.obs": 10,
              "S3.unobs": 1000, "S4.obs": 5, "S4.unobs": 3}
    alloc = {"S1": 100, "S2": 100, "S3": 100, "S4": 150}
    out, notes = sample.allocate(counts, alloc, 0.667)
    assert out["S1"] == 100
    assert out["S2.obs"] == 100 and out["S2.unobs"] == 0      # empty part hands over
    assert out["S3.obs"] == 10 and out["S3.unobs"] == 90      # short part takes all
    assert out["S4.obs"] == 5 and out["S4.unobs"] == 3        # both short
    assert any("S4" in n and "short" in n for n in notes)
    assert any("S2.unobs" in n for n in notes)


def test_draw_is_seeded_and_without_replacement() -> None:
    code = np.zeros((30, 30), dtype="uint8")
    code[:10] = sample.CODES["S1"]
    code[10:] = sample.CODES["S4.unobs"]
    per = {"S1": 20, "S4.unobs": 50}
    a = sample.draw(code, per, np.random.default_rng(7))
    b = sample.draw(code, per, np.random.default_rng(7))
    assert a == b
    cells = [c for _, c in a]
    assert len(cells) == len(set(cells)) == 70
    assert all(code.ravel()[c] == sample.CODES[k] for k, c in a)


def test_frozen_sample_is_never_redrawn(tmp_path: Path | None = None) -> None:
    import tempfile

    root = Path(tmp_path or tempfile.mkdtemp())
    cfg = root / "config.yaml"
    cfg.write_text(
        "event: t\nseed: 1\noutput_dir: .\n"
        "reference: {zip: x.zip, flood_layer: f, code: c}\n"
        "temporal: {mode: cumulative, window: ['2025-01-01', '2025-01-02']}\n"
        "grid: {crs: 'EPSG:32644', resolution_m: 10}\n"
        "aoi: {bbox: [80.0, 8.0, 80.01, 8.01]}\n"
        "evaluation: {flood_fraction_threshold: 0.5, domain_fraction_threshold: 0.5}\n"
        "products: {p: {source: null}}\n"
        "sample: {pilot: {seed: 1, allocation: {S1: 1}}}\n", encoding="utf-8")
    frozen = root / "sample" / "pilot"
    frozen.mkdir(parents=True)
    (frozen / "manifest.yaml").write_text("frozen: true\n", encoding="utf-8")
    try:
        sample.build(cfg, "pilot")
    except FileExistsError as exc:
        assert "never redrawn" in str(exc)
    else:
        raise AssertionError("a frozen sample was redrawn")


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
