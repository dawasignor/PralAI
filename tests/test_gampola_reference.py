"""The Gampola experiment (2026-11-18): the hardcoded reference lookup, the
EMS reference read by attribute with its analysed extent, and the user's
choice of flood-map product for Modules 9-10.

No network, no model. The checks on the real EMSR851 zips run only where the
experiment folder exists (it is untracked); the rest use synthetic data.
Runs under pytest or as a plain script.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import product_choice, references  # noqa: E402
from core.workflows.validation import reference as R  # noqa: E402

GAMPOLA = [80.5460, 7.1368, 80.6310, 7.2450]
KELANI_BASIN = [79.9450, 6.8800, 80.1550, 7.0000]
EXP = Path(__file__).resolve().parents[1] / "experiments"
ZIPS = Path(__file__).resolve().parents[1] / "experiments" / "gampola-2025" / "validation"


# -- the hardcoded lookup ----------------------------------------------------------
def test_the_reference_is_looked_up_from_the_aoi() -> None:
    got = references.resolve(GAMPOLA) if (ZIPS / "config.yaml").is_file() else None
    if got:
        assert got["name"] == "emsr851-aoi09" and got["others"] == ["emsr851-aoi03"]
        assert references.resolve(GAMPOLA, "emsr851-aoi03")["path"].endswith("config_aoi03.yaml")
    assert references.experiment_for(GAMPOLA) == "gampola-2025"
    # The same place drawn a little differently is still Gampola ...
    assert references.experiment_for([80.55, 7.14, 80.63, 7.24]) == "gampola-2025"
    # ... a neighbour that only touches it is not, and gets no reference.
    assert references.experiment_for([80.62, 7.24, 80.75, 7.40]) is None
    assert references.experiment_for([79.94, 6.905, 80.09, 6.978]) == "kelani-lower-2025"
    # The Kelani basin box CONTAINS the lower-Kelani one; matched by overlap
    # over union, each finds its own experiment, not a tie.
    assert references.experiment_for(KELANI_BASIN) == "kelani-basin-2025"
    assert references.same_box(KELANI_BASIN, [79.945, 6.88, 80.155, 7.0])
    assert not references.same_box(KELANI_BASIN, [79.94, 6.905, 80.09, 6.978])
    for bbox, name in (([81.0, 8.0, 81.1, 8.1], ""), (GAMPOLA, "unosat")):
        try:
            references.resolve(bbox, name)
            raise AssertionError(f"{bbox} {name!r} found a reference")
        except references.NoReference as exc:
            assert "reference" in str(exc)


def test_the_kelani_basin_reference_and_what_an_agent_may_send() -> None:
    """The main experiment (2026-11-18): EMSR851 AOI02. Whatever the agent
    sends -- no config, a stale config of another experiment, an invented
    path, the batch's event_id -- the session's own reference is used."""
    import inspect

    from core.workflows.accuracy import accuracy_metrics

    assert "event_id" in inspect.signature(accuracy_metrics).parameters
    if not (EXP / "kelani-basin-2025" / "validation" / "config.yaml").is_file():
        print("  (skipped the file checks: kelani-basin-2025 is not in this checkout)")
        return
    got = references.resolve(KELANI_BASIN)
    assert got["name"] == "emsr851-aoi02" and got["experiment"] == "kelani-basin-2025"
    assert references.belongs(got["path"], "kelani-basin-2025")
    stale = str(EXP / "kelani-lower-2025" / "validation" / "config.yaml")
    assert not references.belongs(stale, "kelani-basin-2025")


def test_the_real_emsr851_aoi02_reference() -> None:
    zip_ = EXP / "kelani-basin-2025" / "validation" / "reference" / "EMSR851_AOI02_DEL_PRODUCT_v1.zip"
    if not zip_.is_file():
        print("  (skipped: the AOI02 zip is not in this checkout)")
        return
    from core.workflows.validation import config

    cfg = config.load(EXP / "kelani-basin-2025" / "validation" / "config.yaml")
    grid = R.grid_for(cfg["aoi"]["bbox"], "EPSG:32644", 10.0)
    ref = R.ingest(cfg, grid)
    km2 = lambda m: m.sum() * 1e-4                          # noqa: E731
    assert abs(km2(ref["flood"]) - 30.13) < 0.2, km2(ref["flood"])
    assert abs(km2(ref["analysed"]) - 157.77) < 0.5, km2(ref["analysed"])
    assert ref["meta"]["image"]["sensor"] == "PlanetScope"
    # Window filter: OPERA files listed are inside 26 Nov - 4 Dec only.
    for key in ("dswx_hls", "dswx_s1"):
        for f in cfg["products"][key]["files"]:
            assert "2025-11-26" <= f["datetime"][:10] <= "2025-12-04", f


def test_accuracy_with_no_config_and_no_reference_says_so() -> None:
    from core import session
    from core.session import Place, SessionRecord
    from core.workflows.accuracy import accuracy_metrics

    record = SessionRecord(session_id="t")
    record.place = Place(display="nowhere", bbox=(10.0, 10.0, 10.1, 10.1))
    session._CURRENT.set(record)
    try:
        accuracy_metrics()
        raise AssertionError("ran without a reference")
    except references.NoReference as exc:
        assert "no reference flood map is configured for this area" in str(exc)
    finally:
        session._CURRENT.set(None)


# -- reading the reference by attribute --------------------------------------------
def _dbf(rows: list[dict[str, str]], fields: list[tuple[str, int]]) -> bytes:
    rec_len = 1 + sum(w for _, w in fields)
    head = struct.pack("<BBBBIHH20x", 3, 125, 11, 18, len(rows), 32 + 32 * len(fields) + 1, rec_len)
    desc = b"".join(name.encode().ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([w]) + b"\0" * 15
                    for name, w in fields)
    body = b"".join(b" " + b"".join(r.get(n, "").encode().ljust(w) for n, w in fields)
                    for r in rows)
    return head + desc + b"\r" + body + b"\x1a"


def test_the_attribute_table_is_read_and_filtered() -> None:
    rows = [{"notation": "Flooded area", "event_type": "5-Flood"},
            {"notation": "Flood trace", "event_type": "5-Flood"},
            {"notation": "Not Applicable", "event_type": "6-Mass Movement"}]
    read = R.read_dbf(_dbf(rows, [("notation", 20), ("event_type", 16)]))
    assert read == rows
    keep = {"notation": ["Flooded area", "Flood trace"], "event_type": ["5-Flood"]}
    assert [R._keep(r, keep) for r in read] == [True, True, False]
    try:
        R._keep(read[0], {"damage_gra": ["Destroyed"]})
        raise AssertionError("a missing field was accepted")
    except ValueError as exc:
        assert "no field" in str(exc)


def test_the_real_emsr851_references() -> None:
    """Against the delivered zips: flood trace counts, the landslide does not,
    and only the analysed area takes part. Skipped where they are absent."""
    if not (ZIPS / "reference" / "EMSR851_AOI09_GRA_PRODUCT_v1.zip").is_file():
        print("  (skipped: the EMSR851 zips are not in this checkout)")
        return
    from core.workflows.validation import config

    cfg = config.load(ZIPS / "config.yaml")
    grid = R.grid_for(cfg["aoi"]["bbox"], "EPSG:32644", 10.0)
    ref = R.ingest(cfg, grid)
    km2 = lambda m: m.sum() * 1e-4                          # noqa: E731
    assert abs(km2(ref["flood"]) - 0.84) < 0.02, km2(ref["flood"])      # 0.41 + 0.43
    assert abs(km2(ref["analysed"]) - 28.13) < 0.1, km2(ref["analysed"])
    slide = [e for e in ref["meta"]["excluded_layers"] if e.get("where")]
    assert slide and slide[0]["pixels"] > 0
    assert not (ref["flood"] & ref["excluded"]).any() or km2(ref["flood"] & ref["excluded"]) < 0.01
    cfg_flooded_only = dict(cfg, reference=dict(cfg["reference"],
                                                flood_where={"notation": ["Flooded area"]}))
    only = R.ingest(cfg_flooded_only, grid)
    assert km2(only["flood"]) < km2(ref["flood"]) - 0.3      # the traces were counted


def test_the_analysed_extent_limits_the_domain() -> None:
    """Outside where the reference looked is out of the domain, never dry."""
    import numpy as np

    from core.workflows.validation import domain

    grid = R.grid_for([80.0, 7.0, 80.01, 7.01], "EPSG:32644", 100.0)
    shape = (grid["height"], grid["width"])
    analysed = np.zeros(shape, dtype=bool)
    analysed[:, : shape[1] // 2] = True
    flood = np.zeros(shape, dtype=bool)
    flood[0, 0] = True
    saved = (domain._sea, domain._permanent_water)
    domain._sea = lambda bbox, grid, exclude: np.zeros(shape, dtype=bool)
    domain._permanent_water = lambda cfg, grid: (np.zeros(shape, dtype=bool), {})
    try:
        out = domain.build({"aoi": {"bbox": [80.0, 7.0, 80.01, 7.01]}}, grid,
                           {"flood": flood, "excluded": np.zeros(shape, dtype=bool),
                            "analysed": analysed})
    finally:
        domain._sea, domain._permanent_water = saved
    assert not out["domain"][:, shape[1] // 2 + 1:].any()
    assert out["census"]["outside_reference_analysed_km2"] > 0


# -- the user's choice of product ----------------------------------------------------
def test_a_product_choice_is_read_narrowly() -> None:
    read = product_choice.read
    assert read("Use the MODIS flood map for the essential services") == "mcdwd"
    assert read("map it with VIIRS instead of GFM") == "vcdwd"
    assert read("use OPERA DSWx-S1") == "dswx_s1"
    assert read("switch to DSWx-HLS") == "dswx_hls"
    assert read("go back to the best-ranked product") == "best"
    # Naming products is not choosing one.
    assert read("compare GFM and MODIS against the reference") is None
    assert read("how did MODIS do?") is None
    assert read("use GFM or MODIS") is None                   # two left: no choice


def test_the_module_maps_the_chosen_product_else_the_best() -> None:
    from core.experiment import Experiment
    from core.outputs import Inputs, module_product

    with tempfile.TemporaryDirectory() as tmp:
        store = Experiment("choice-2025", root=Path(tmp))
        store.create(place="p", window="2025-11-26 to 2025-12-04")
        assert module_product(store, Inputs(store))[0] is None          # nothing ranked
        store.cache_input("rank_flood_products", {"ranking": [
            {"id": "gfm", "alternative": "GFM (Sentinel-1)", "rank": 1},
            {"id": "mcdwd", "alternative": "MODIS MCDWD", "rank": 3}]})
        assert module_product(store, Inputs(store)) == ("gfm", "GFM (Sentinel-1)", "best-ranked")
        product_choice.set_choice(store, "mcdwd", "use MODIS")
        assert module_product(store, Inputs(store)) == ("mcdwd", "MODIS MCDWD", "chosen")
        product_choice.clear(store)
        assert module_product(store, Inputs(store))[2] == "best-ranked"
        # A chosen product needs no ranking.
        empty = Experiment("choice2-2025", root=Path(tmp))
        empty.create(place="p", window="2025-11-26 to 2025-12-04")
        product_choice.set_choice(empty, "vcdwd", "use VIIRS")
        assert module_product(empty, Inputs(empty))[:2] == ("vcdwd", "VIIRS VCDWD")


def test_the_choice_reaches_every_prompt_and_leaves_with_the_aoi() -> None:
    from core.session import Place, SessionRecord, Window

    record = SessionRecord(session_id="t", flood_product="mcdwd")
    record.place = Place(display="Gampola", bbox=tuple(GAMPOLA))
    record.window = Window(start="2025-11-26", end="2025-12-04")
    assert "Flood map product: MODIS MCDWD -- the user's choice" in record.header()
    again = SessionRecord.from_dict(json.loads(json.dumps(record.to_dict())))
    assert again.flood_product == "mcdwd"

    from core.session_store import SessionStore

    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore.__new__(SessionStore)
        store.dir = Path(tmp)
        store.inputs_dir = Path(tmp) / "inputs"
        store.inputs_dir.mkdir()
        (store.inputs_dir / "x.json").write_text("{}")
        product_choice.set_choice(store, "mcdwd", "use MODIS")
        moved = store.archive("AOI changed")
        assert product_choice.get(store) is None
        assert (moved / "flood_product.json").is_file()


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
