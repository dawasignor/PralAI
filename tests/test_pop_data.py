"""get_pop_data: AOI population from WorldPop Global2, on a synthetic raster.

    python tests/test_pop_data.py           # no model, no network, no cost
    python tests/test_pop_data.py --live    # also the real LKA 2025 file (free, ~18 MB once)

Held still here:

* the total is the sum of the pixels whose CENTRE lies inside the box, and the
  range runs from that total less its edge row/column to that total plus the
  ring just outside;
* no-data and negative pixels are zero people, never a number;
* a box partly outside the country raster says how much is not counted, and a
  box wholly outside it raises;
* a year outside 2015-2030, or a year the hub does not list, raises;
* the standing uncertainty (data source, method) is written, for this tool
  and every other, and travels with the model-uncertainty todo;
* the report reads the new payload, and refuses to re-read a payload stored by
  the retired census tool.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import logbook, uncertainty, workflows                     # noqa: E402
from core.workflows import _fetch as F, population                    # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
RES = 0.1                    # a coarse fake grid, so pixel arithmetic is exact


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def _raster(path: Path) -> None:
    """A 10 x 10 grid from (80.0 W edge, 7.0 N edge), every pixel 1 person,
    except one no-data pixel (-99999) and one negative pixel."""
    import rasterio
    from rasterio.transform import from_origin

    arr = np.ones((10, 10), dtype="float32")
    arr[4, 4] = -99999.0
    arr[5, 5] = -3.0
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1,
                       dtype="float32", crs="EPSG:4326", nodata=-99999.0,
                       transform=from_origin(80.0, 7.0, RES, RES)) as ds:
        ds.write(arr, 1)


class _Fake:
    """Swap the hub lookup and the download for the synthetic file."""

    def __init__(self, path: Path, years=(2025,)):
        self.path, self.years = path, years

    def __enter__(self):
        self.saved = (F.get_json, F.cached_file)
        F.get_json = lambda url: {"data": [
            {"popyear": str(y), "files": [f"https://x/{y}/lka_pop_{y}_CN_100m_R2025A_v1.tif"],
             "url_summary": "https://hub/summary"} for y in self.years]}
        F.cached_file = lambda url, sub, name, **kw: (self.path, {
            "url": url, "retrieved_at": "2026-10-30T00:00:00Z", "cached": False})
        return self

    def __exit__(self, *exc):
        F.get_json, F.cached_file = self.saved


def raises(fn, word: str) -> bool:
    try:
        fn()
    except ValueError as exc:
        return word in str(exc)
    return False


def test_sum_and_range(tif: Path) -> None:
    # Pixel centres sit at 80.05, 80.15, ... and 6.95, 6.85, ... The box
    # 80.2-80.6 x 6.4-6.8 holds centres 80.25..80.55 (4 cols) and
    # 6.75..6.45 (4 rows): rows 2-5, cols 2-5, 16 pixels, one no-data and one
    # negative among them -> 14 people.
    with _Fake(tif):
        d = population.get_pop_data([80.2, 6.4, 80.6, 6.8], "LKA", write_figure=False)
    check("pixels whose centre is inside the box are summed", d["population"] == 14,
          str(d["population"]))
    check("no-data and negative pixels are zero people, counted as no-data",
          d["pixels"] == {"counted": 16, "with_people": 14, "no_data": 2}, str(d["pixels"]))
    # Edge of the 4 x 4 window: 12 pixels, one of them the negative (5, 5),
    # so 11 people -> low = 14 - 11 = 3. Ring outside it (6 x 6 less 4 x 4 =
    # 20 pixels, all 1) -> high = 34.
    check("the range runs from less the edge to plus the outside ring",
          d["range"] == [3, 34], str(d["range"]))
    check("the box lies wholly inside the raster", d["coverage"]["bbox_in_raster"] == 1.0)
    check("the vintage and the source name the release and year",
          d["vintage"] == "WorldPop Global2 R2025A v1, 2025"
          and d["sources"][0]["licence"] == "CC BY 4.0")
    keys = {i["key"] for lvl in ("input", "tool") for i in d["uncertainty"][lvl]}
    check("the uncertainty block says modelled, constrained and the edge band",
          {"modelled", "constrained", "edge_band"} <= keys, str(keys))


def test_outside(tif: Path) -> None:
    with _Fake(tif):
        d = population.get_pop_data([80.8, 6.4, 81.2, 6.8], "LKA", write_figure=False)
    check("a box half outside the raster counts only what is inside",
          d["coverage"]["bbox_in_raster"] == 0.5 and d["population"] == 8,
          f"{d['coverage']} {d['population']}")
    check("... and says how much of the box is not counted",
          any(i["key"] == "outside_raster" for i in d["uncertainty"]["tool"]))
    with _Fake(tif):
        check("a box wholly outside the raster raises",
              raises(lambda: population.get_pop_data([90, 10, 91, 11], "LKA"), "does not overlap"))
        check("a year outside 2015-2030 raises",
              raises(lambda: population.get_pop_data([80.2, 6.4, 80.6, 6.8], "LKA", 2014), "2015"))
    with _Fake(tif, years=(2020,)):
        check("a year the hub does not list raises, naming the release",
              raises(lambda: population.get_pop_data([80.2, 6.4, 80.6, 6.8], "LKA", 2025),
                     "R2025A"))


def test_standing_uncertainty() -> None:
    u = workflows.tool_uncertainty("get_pop_data")
    check("data source and method statements are written",
          bool(u["data_source"]) and bool(u["method"]))
    check("model uncertainty is a todo: the same event 10 times uncached",
          u["model"]["status"] == "todo" and "10 times uncached" in u["model"]["how"])
    check("every registered tool has both statements written",
          workflows.uncertainty_todo() == [], str(workflows.uncertainty_todo()))
    check("the retired population tool points at get_pop_data",
          workflows.resolve("fetch_population") == (None, "retired")
          and workflows.RETIRED["fetch_population"][0] == "get_pop_data")
    lines = uncertainty.standing_lines("get_pop_data")
    check("Entro's register opens each tool with DATA, METHOD and MODEL lines",
          [ln.split()[0] for ln in lines] == ["DATA", "METHOD", "MODEL"])


def test_report(tif: Path, root: Path) -> None:
    from core import report_render
    from core.experiment import Experiment

    with _Fake(tif):
        d = population.get_pop_data([80.2, 6.4, 80.6, 6.8], "LKA", write_figure=False)
    run = Experiment("pop-test", root=root / "exp")
    run.create(place="p", window="2025-11-24 to 2025-12-01")
    run.cache_input("population", d)
    f = report_render.f_people_aoi(report_render.Ctx(run))
    check("the report reads the WorldPop total with its range",
          f.value == 14 and "range 3 to 34" in f.source, f"{f.value} {f.source}")
    run.cache_input("population", {"country_iso3": "LKA", "source_used": "census",
                                   "estimate": {"population": 900}})
    f = report_render.f_people_aoi(report_render.Ctx(run))
    check("a payload from the retired census tool is not re-read",
          f.status == "not_known" and "retired census tool" in f.reason, f.reason)


def test_population_map(tif: Path, root: Path) -> None:
    """get_pop_data draws its map (2026-11-18), with the flood extent on top,
    into output_dir -- and the per-pixel shares never reach the payload."""
    import rasterio
    from rasterio.transform import from_origin

    extent = root / "extent.tif"
    codes = np.full((40, 40), 255, dtype="uint8")       # mostly not observed
    codes[20:, :] = 0                                   # the south half seen dry
    codes[30:34, 10:14] = 1                             # a small flood
    with rasterio.open(extent, "w", driver="GTiff", height=40, width=40, count=1,
                       dtype="uint8", crs="EPSG:4326", nodata=None,
                       transform=from_origin(80.2, 6.8, 0.01, 0.01)) as ds:
        ds.write(codes, 1)
    out = root / "maps"
    with _Fake(tif):
        d = population.get_pop_data([80.2, 6.4, 80.6, 6.8], "LKA", extent_raster=str(extent),
                                    output_dir=str(out), event_id="ev")
    png = Path((d.get("figure") or {}).get("path", ""))
    check("the population map is drawn into output_dir/event_id",
          png == out / "ev" / "population_map.png" and png.is_file(), str(d.get("figure") or d.get("map")))
    check("its caption names WorldPop, the year and the flood overlay",
          "WorldPop" in d["figure"]["caption"] and "2025" in d["figure"]["caption"]
          and "did not observe" in d["figure"]["caption"])
    check("no array travels in the payload", "_shares" not in d["flood_extent"])
    check("people on unobserved land are still counted apart",
          d["flood_extent"]["people_not_observed"] > 0)
    with _Fake(tif):
        quiet = population.get_pop_data([80.2, 6.4, 80.6, 6.8], "LKA", write_figure=False)
    check("write_figure=False draws nothing", "figure" not in quiet and "map" not in quiet)


def test_live() -> None:
    d = population.get_pop_data([79.94, 6.905, 80.09, 6.978], "LKA", 2025, write_figure=False)
    lo, hi = d["range"]
    check("live: Kelani AOI population from WorldPop, inside its range",
          d["population"] > 0 and lo <= d["population"] <= hi,
          f"{d['population']:,} ({lo:,} to {hi:,})")


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        with tempfile.TemporaryDirectory() as raw:
            tif = Path(raw) / "fake.tif"
            _raster(tif)
            test_sum_and_range(tif)
            test_outside(tif)
            test_standing_uncertainty()
            test_report(tif, Path(raw))
            test_population_map(tif, Path(raw))
        if "--live" in sys.argv:
            test_live()
    finally:
        logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
