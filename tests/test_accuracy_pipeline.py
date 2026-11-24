"""The accuracy_metrics pipeline end to end, on a synthetic event.

    pytest tests/test_accuracy_pipeline.py
    python tests/test_accuracy_pipeline.py

Everything is built in a temporary folder -- a zipped reference shapefile with
a hole, a JRC-style permanent-water raster, product GeoTIFFs and their no-data
masks on a 20 m grid, a TOPSIS CSV and a config -- so the test needs neither the
network nor the real UNOSAT file. The sea mask (a network read) is stood in.

Held still: holes are cut from the reference; the domain census; a product
that matches the reference agrees near-perfectly; an empty product is all
omission; no-data masks lower coverage instead of adding non-flood; the
cumulative union; the time window applies to local files; a product that
cannot be read keeps its row with the reason; and a config value that is
missing is refused, never invented.
"""

from __future__ import annotations

import csv
import json
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rasterio                                                 # noqa: E402
from rasterio.transform import from_origin                      # noqa: E402
from rasterio.warp import transform as warp                     # noqa: E402

from core.workflows import accuracy, landmask                    # noqa: E402
from core.workflows.validation import config as cfg_mod          # noqa: E402

BBOX = [80.000, 7.000, 80.010, 7.010]          # about 1.1 km square
WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
             '6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
             'UNIT["Degree",0.0174532925199433]]')
# Reference flood: a square with a square hole (outer clockwise, hole
# counter-clockwise -- the shapefile rule).
OUTER = [(80.002, 7.008), (80.008, 7.008), (80.008, 7.002), (80.002, 7.002), (80.002, 7.008)]
HOLE = [(80.004, 7.004), (80.006, 7.004), (80.006, 7.006), (80.004, 7.006), (80.004, 7.004)]


def shp_bytes(rings: list[list[tuple[float, float]]]) -> bytes:
    """One Polygon record holding `rings` -- a minimal valid .shp."""
    points = [p for ring in rings for p in ring]
    parts, cursor = [], 0
    for ring in rings:
        parts.append(cursor)
        cursor += len(ring)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    box = struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
    content = (struct.pack("<i", 5) + box + struct.pack("<ii", len(rings), len(points))
               + struct.pack(f"<{len(parts)}i", *parts)
               + b"".join(struct.pack("<2d", *p) for p in points))
    record = struct.pack(">ii", 1, len(content) // 2) + content
    header = (struct.pack(">i", 9994) + b"\0" * 20
              + struct.pack(">i", (100 + len(record)) // 2)
              + struct.pack("<ii", 1000, 5) + box + b"\0" * 32)
    return header + record


def write_zip(path: Path, layers: dict[str, list]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, rings in layers.items():
            z.writestr(f"REF/{name}.shp", shp_bytes(rings))
            z.writestr(f"REF/{name}.prj", WGS84_PRJ)


def utm_grid(res: float = 20.0):
    """A 20 m product grid offset 7 m from the 10 m reference lattice, as a real
    product grid is (GFM's Equi7 does not align with UTM). On an aligned grid
    every edge pixel holds exactly half flood, and the plan's strict "exceeds
    0.5" drops them all -- a property of the alignment, not of the product."""
    xs, ys = warp("EPSG:4326", "EPSG:32644", [BBOX[0], BBOX[2]], [BBOX[3], BBOX[1]])
    west, north = np.floor(xs[0] / res) * res - res + 7.0, np.ceil(ys[0] / res) * res + res - 7.0
    width = int(np.ceil((xs[1] - west) / res)) + 2
    height = int(np.ceil((north - ys[1]) / res)) + 2
    return from_origin(west, north, res, res), (height, width)


def write_tif(path: Path, array: np.ndarray, transform, crs: str, nodata=None) -> None:
    with rasterio.open(path, "w", driver="GTiff", width=array.shape[1], height=array.shape[0],
                       count=1, dtype=array.dtype, crs=crs, transform=transform,
                       nodata=nodata) as dst:
        dst.write(array, 1)


def inside_outer_not_hole(transform, shape) -> np.ndarray:
    """Truth on the product grid: pixel centre in OUTER and not in HOLE."""
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    x = transform.c + (cols + 0.5) * transform.a
    y = transform.f + (rows + 0.5) * transform.e
    lon, lat = warp("EPSG:32644", "EPSG:4326", x.ravel().tolist(), y.ravel().tolist())
    lon, lat = np.array(lon).reshape(shape), np.array(lat).reshape(shape)
    outer = (lon > 80.002) & (lon < 80.008) & (lat > 7.002) & (lat < 7.008)
    hole = (lon > 80.004) & (lon < 80.006) & (lat > 7.004) & (lat < 7.006)
    return outer & ~hole


def build(tmp: Path, *, sea_cols: int = 0) -> Path:
    """All inputs and a config. Returns the config path."""
    write_zip(tmp / "ref.zip", {"Flood": [OUTER, HOLE],
                                "Cloud": [[(79.5, 6.5), (79.5, 6.6), (79.6, 6.6), (79.6, 6.5), (79.5, 6.5)]]})

    # Permanent water: class 3 in the south-west corner of the AOI.
    water = np.zeros((40, 40), dtype="uint8")
    water[34:, :6] = 3
    write_tif(tmp / "water_class.tif", water, from_origin(80.0, 7.01, 0.00025, 0.00025),
              "EPSG:4326", nodata=0)

    transform, shape = utm_grid()
    truth = inside_outer_not_hole(transform, shape)
    perfect = np.where(truth, 1, 0).astype("uint8")
    write_tif(tmp / "perfect.tif", perfect, transform, "EPSG:32644", nodata=255)
    write_tif(tmp / "empty.tif", np.zeros(shape, dtype="uint8"), transform, "EPSG:32644", nodata=255)
    # Half of the flood on one date, the rest seen dry: the union must restore it.
    first_half = perfect.copy()
    first_half[:, shape[1] // 2:] = 0
    second_half = perfect.copy()
    second_half[:, : shape[1] // 2] = 0
    write_tif(tmp / "half_a.tif", first_half, transform, "EPSG:32644", nodata=255)
    write_tif(tmp / "half_b.tif", second_half, transform, "EPSG:32644", nodata=255)
    mask = np.zeros(shape, dtype="uint8")
    mask[:, : shape[1] // 2] = 1                                   # left half excluded
    write_tif(tmp / "mask.tif", mask, transform, "EPSG:32644", nodata=255)

    with (tmp / "topsis.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "score", "rank"])
        for i, key in enumerate(["perfect", "masked", "union", "empty", "nodata_src"], 1):
            w.writerow([key, 1 - i / 10, i])

    enc = {"flood": [1], "nonflood": [0], "nodata": [255]}
    on = "2025-11-27T00:00:00Z"
    config = {
        "event": "synthetic", "seed": 1, "output_dir": "out",
        "reference": {"code": "TEST1", "zip": "ref.zip", "flood_layer": "Flood",
                      "exclude_layers": ["Cloud"], "credit": "test"},
        "temporal": {"mode": "cumulative", "window": ["2025-11-26", "2025-12-02"]},
        "grid": {"crs": "EPSG:32644", "resolution_m": 10},
        "aoi": {"bbox": BBOX},
        "domain": {"exclude_wbm_classes": ["ocean"],
                   "permanent_water": {"raster": "water_class.tif", "classes": [3]}},
        "evaluation": {"flood_fraction_threshold": 0.5, "domain_fraction_threshold": 0.5},
        "topsis": {"path": "topsis.csv"},
        "assumptions": ["synthetic"],
        "products": {
            "perfect": {"label": "Perfect", "source": "files", "encoding": enc,
                        "files": [{"path": "perfect.tif", "datetime": on}]},
            "masked": {"label": "Masked", "source": "files", "encoding": enc,
                       "files": [{"path": "perfect.tif", "datetime": on,
                                  "nodata_masks": [{"path": "mask.tif", "values": [1]}]}]},
            "union": {"label": "Union", "source": "files", "encoding": enc,
                      "files": [{"path": "half_a.tif", "datetime": on},
                                {"path": "half_b.tif", "datetime": "2025-12-01T00:00:00Z"},
                                {"path": "empty.tif", "datetime": "2025-12-20T00:00:00Z"}]},
            "empty": {"label": "Empty", "source": "files", "encoding": enc,
                      "files": [{"path": "empty.tif", "datetime": on}]},
            "nodata_src": {"label": "No source", "source": None,
                           "not_evaluated_reason": "no archive for the event"},
            "no_encoding": {"label": "No encoding", "source": "files",
                            "files": [{"path": "perfect.tif", "datetime": on}]},
        },
    }
    path = tmp / "config.yaml"
    import yaml
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def run(config_path: Path, sea_cols: int = 0) -> dict:
    original = landmask.water_codes

    def fake(bbox, plan):
        codes = np.zeros(plan.shape, dtype="uint8")
        if sea_cols:
            codes[:, :sea_cols] = 1                             # ocean on the west edge
        return codes

    landmask.water_codes = fake
    try:
        return accuracy.accuracy_metrics(config=str(config_path))
    finally:
        landmask.water_codes = original


def rows(payload: dict) -> dict[str, dict]:
    return {r["id"]: r for r in payload["table"]}


def products(payload: dict) -> dict[str, dict]:
    return {p["id"]: p for p in payload["products"]}


def test_pipeline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        payload = run(build(tmp))
        t, p = rows(payload), products(payload)

        # Reference: the hole is cut. 6x6 mm-degree square minus 2x2 = 32/36 of it.
        ref_km2 = payload["domain"]["reference_flood_km2"]
        square_km2 = (0.006 * 110.6) * (0.006 * 110.4)
        assert abs(ref_km2 - square_km2 * 32 / 36) / (square_km2 * 32 / 36) < 0.05, ref_km2
        assert payload["reference"]["holes_in_aoi"] == 1

        census = payload["domain"]
        assert census["permanent_water_removed_km2"] > 0
        assert census["reference_exclusions_removed_km2"] == 0      # cloud is far away
        assert census["sea_removed_km2"] == 0

        perfect = p["perfect"]["metrics"]
        assert perfect["coverage"] > 0.99
        assert perfect["f1"] > 0.95 and perfect["mcc"] > 0.95, perfect

        empty = p["empty"]["metrics"]
        assert empty["recall"] == 0.0 and empty["precision"] is None and empty["tp"] == 0

        masked = p["masked"]["metrics"]
        assert 0.4 < masked["coverage"] < 0.6, masked["coverage"]   # half the grid masked
        assert masked["compared_pixels"] < perfect["compared_pixels"]
        assert masked["f1"] > 0.9                                    # masked, not wrong

        union = p["union"]
        assert union["metrics"]["f1"] > 0.95                         # the halves recombine
        assert union["product"]["observations_with_data"] == 2      # the 20 Dec file is outside

        assert t["nodata_src"]["status"] == "not evaluated"
        assert "no archive" in t["nodata_src"]["note"]
        assert t["no_encoding"]["status"] == "not evaluated"
        assert "not guessed" in t["no_encoding"]["note"]

        order = [r["id"] for r in payload["table"]]
        assert order.index("empty") > order.index("perfect")
        assert all(r["status"] == "evaluated" for r in payload["table"][:4])
        assert t["perfect"]["metric_rank"] == 1 and t["perfect"]["topsis_rank"] == 1
        assert t["nodata_src"]["topsis_rank"] == 5

        # The reference by its own code, never a fixed publisher (2026-11-18:
        # the note said "UNOSAT" for every reference, EMS included).
        assert payload["note"].startswith("Agreement with the reference ")
        assert "not accuracy against ground truth" in payload["note"]
        assert "unosat" not in payload["note"].lower()
        md = Path(payload["outputs"]["markdown"]).read_text(encoding="utf-8")
        assert "| Product | Coverage |" in md and "not evaluated: no archive" in md
        assert Path(payload["outputs"]["csv"]).is_file()
        assert json.loads(Path(payload["outputs"]["payload"]).read_text(encoding="utf-8"))["table"]


def test_sea_is_removed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        payload = run(build(tmp), sea_cols=20)
        assert payload["domain"]["sea_removed_km2"] > 0
        assert payload["domain"]["domain_km2"] < payload["domain"]["aoi_km2"]


def test_missing_config_value_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = build(tmp)
        text = path.read_text(encoding="utf-8").replace("mode: cumulative", "mode: TODO")
        path.write_text(text, encoding="utf-8")
        try:
            cfg_mod.load(path)
        except ValueError as exc:
            assert "temporal.mode" in str(exc)
            return
        raise AssertionError("a TODO placeholder was accepted")


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
