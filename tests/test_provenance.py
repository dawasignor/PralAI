"""Source and acquisition date on every file and figure the tools write (BUG-020).

    python tests/test_provenance.py        # no model, no network, no cost

The rule (user decision 2026-10-26): an NDVI GeoTIFF opened on its own, or its
PNG pasted into a chapter, says where the data came from and when it was
acquired -- "acquired 11 Aug 2026", a readable local date with no time (user
decision 2026-11-01) -- without the report beside it.
Covers the tags `core/dating.py` writes, the hook in `monty_graph._run_one`
that tags whatever GeoTIFFs a tool returns, the figure line read back from
the tags, and the honest line for a raster written before the tags existed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                             # noqa: E402
from rasterio.transform import from_origin                     # noqa: E402

from core import dating, monty_graph                           # noqa: E402
from core.figures import render                                # noqa: E402
from core.workflows import _raster as R                        # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []

SCENE = {"id": "S2A_T30VVH_20260811T113338_L2A", "is_mosaic": False,
         "datetime": "2026-08-11T11:35:55.823000Z"}
BBOX = [-3.30, 55.90, -3.10, 56.00]
LONDON = "Europe/London"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def _ndvi(path: Path) -> None:
    plan = R.GridPlan(None, from_origin(500000, 6200000, 20, 20), "EPSG:32630",
                      (40, 40), 20.0)
    arr = np.linspace(0.1, 0.8, 1600, dtype="float32").reshape(40, 40)
    R.write_index(str(path), arr, plan)


def _payload(path: Path) -> dict:
    return {"aoi": {"bbox": BBOX}, "scene": SCENE,
            "source": {"catalogue": "https://earth-search.aws.element84.com/v1",
                       "collection": "sentinel-2-c1-l2a"},
            "indices": {"ndvi": {"raster": {"path": str(path).replace("\\", "/")}}}}


def test_tags(tmp: Path) -> None:
    tif = tmp / "ndvi.tif"
    _ndvi(tif)
    payload = _payload(tif)
    done = dating.tag_rasters("lulc", payload, LONDON)
    tags = dating.read_tags(tif)
    check("the GeoTIFF a result names is tagged", done == [str(tif).replace("\\", "/")], str(done))
    check("source names Sentinel-2 L2A and the scene id",
          "Sentinel-2 L2A" in tags.get("DATA_SOURCE", "") and SCENE["id"] in tags["DATA_SOURCE"],
          tags.get("DATA_SOURCE", ""))
    # Acquisitions carry their LOCAL time since 2026-11-19 (user: "time is an
    # important factor"); the 2026-11-01 "date, no time" rule still holds for
    # retrievals and vintages.
    check("a readable local date and local time for the scene",
          "acquired 11 Aug 2026 at 12:35 (local time, UTC+01:00, Europe/London)"
          in tags.get("DATED", ""), tags.get("DATED", ""))
    check("machine-readable UTC instant", tags.get("ACQUISITION_UTC", "").startswith(
        "2026-08-11T11:35:55"), tags.get("ACQUISITION_UTC", ""))
    check("zone and tool recorded", tags.get("AOI_TIMEZONE") == LONDON
          and tags.get("WRITTEN_BY_TOOL") == "lulc", str(tags))
    no_zone = dating.provenance("lulc", payload, None)[dating.TAG_DATED]
    check("no zone -> the UTC clock, marked so, never passed off as local",
          "11 Aug 2026 at 11:35 (UTC; the local time zone is not known)" in no_zone, no_zone)
    late = dating.stamp("2026-08-11T23:30:00Z", "Asia/Bangkok")
    check("the date is the AOI's local date", late == "12 Aug 2026", late)
    check("a bare date is not moved a day west of Greenwich",
          dating.stamp("2021-01-01", "America/New_York") == "1 Jan 2021"
          or dating.day("2021-01-01", "America/New_York") == "1 Jan 2021")
    check("the hour only when asked for",
          dating.stamp("2026-08-11T11:35:55Z", LONDON, time=True)
          == "2026-08-11 11:35 UTC (12:35 BST, UTC+01:00, Europe/London)")
    check("a remote tile path is never 'tagged'",
          dating.raster_paths({"url": "https://x/y.tif", "local": str(tif)}) == [str(tif)])


def test_figure(tmp: Path) -> None:
    tif = tmp / "ndvi.tif"
    drawn = render(tif, tmp / "ndvi.png", name="ndvi")
    line = drawn.get("provenance", "")
    check("the figure carries 'Source: ... | ... acquired <date>' from the tags",
          line.startswith("Source: Sentinel-2 L2A") and "11 Aug 2026" in line, line)
    bare = tmp / "bare.tif"
    _ndvi(bare)
    drawn = render(bare, tmp / "bare.png", name="ndvi")
    check("an untagged raster says so on the figure",
          "not recorded in this raster" in drawn.get("provenance", ""),
          drawn.get("provenance", ""))


def test_hook(tmp: Path) -> None:
    """A tool returning a GeoTIFF gets it tagged by the contract wrapper."""
    tif = tmp / "hook" / "ndvi.tif"

    def fake_lulc(bbox: list) -> dict:
        _ndvi(tif)
        return _payload(tif)

    # The zone is looked up from the bbox centre; seeded so no network is used.
    key = (round((BBOX[1] + BBOX[3]) / 2, 2), round((BBOX[0] + BBOX[2]) / 2, 2))
    dating._ZONES[key] = LONDON
    entry = {"function": "fetch_lulc_data", "params": {"bbox": BBOX},
             "workflow": SimpleNamespace(fn=fake_lulc), "error": None}
    out = monty_graph._run_one(entry)
    tags = dating.read_tags(tif)
    check("the tool's result succeeds", out["status"] == "success", out.get("message", ""))
    check("the run block lists the tagged raster",
          out["output"]["run"].get("rasters_tagged") == [str(tif).replace("\\", "/")],
          str(out["output"]["run"].get("rasters_tagged")))
    check("the file written by the tool carries source and date",
          "acquired 11 Aug 2026" in tags.get("DATED", "") and tags.get("WRITTEN_BY_TOOL") == "lulc",
          str(tags))


def test_lines() -> None:
    weather = {"meta": {"bbox": BBOX, "window_utc": ["2026-08-14T00:00Z", "2026-08-23T23:00Z"],
                        "source": {"model": "ecmwf_ifs -- ECMWF IFS 9 km",
                                   "model_id": "ecmwf_ifs",
                                   "endpoint": "archive-api.open-meteo.com/v1/archive",
                                   "retrieved_at": "2026-10-26T08:00:00Z"}}}
    line = dating.figure_line_for("weather_grid", weather, LONDON)
    check("weather figure line names the model and dates the model hours",
          "ECMWF IFS 9 km" in line and "14-23 Aug 2026 (UTC days)" in line, line)
    terrain = {"source": {"dataset": "Copernicus DEM GLO-30",
                          "url": "https://copernicus-dem-30m.s3.amazonaws.com/x.tif"}}
    line = dating.figure_line_for("terrain", terrain, LONDON)
    check("a static product gives its period, not a pass",
          "Copernicus DEM GLO-30" in line and "2011-2015" in line, line)
    line = dating.figure_line_for("rank_flood_products", {}, LONDON)
    check("a derived result says it has no acquisition of its own",
          "no acquisition of its own" in line, line)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_tags(Path(tmp))
        test_figure(Path(tmp))
        test_hook(Path(tmp))
    test_lines()
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
