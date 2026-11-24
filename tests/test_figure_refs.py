"""Figure placeholders and the endpoint that serves them.

    python tests/test_figure_refs.py        # no model, no network, no cost

Builds a throwaway experiments folder, so it does not depend on the real
experiments/ (gitignored, and present only on the development machine). Covers
the name rule (region-year-figure), the index as the only list of what may be
served, the refusal of anything that could leave the figures folder, the block
Argus is given, the HTTP route, and the citadel -> chanakya rename the console
sees.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import console_server as api                          # noqa: E402
from core import figure_refs                                   # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def build(root: Path) -> None:
    """kelani-2025 with two listed figures and one PNG nobody listed."""
    figures = root / "kelani-2025" / "figures"
    figures.mkdir(parents=True)
    for name in ("bui", "ndvi_class", "unlisted"):
        (figures / f"{name}.png").write_bytes(PNG)
    (figures / "index.json").write_text(json.dumps([
        {"name": "bui", "caption": "BUI -- built-up index."},
        {"name": "ndvi_class", "caption": "NDVI classes."},
        {"name": "ghost", "caption": "Listed, but no PNG."},
    ]), encoding="utf-8")
    # A name that is not region-year: has figures, has no placeholder.
    odd = root / "kelani-2025-west" / "figures"
    odd.mkdir(parents=True)
    (odd / "bui.png").write_bytes(PNG)
    (odd / "index.json").write_text(json.dumps([{"name": "bui"}]),
                                    encoding="utf-8")


def refused(name: str, root: Path) -> bool:
    try:
        figure_refs.resolve(name, root)
    except LookupError:
        return True
    return False


def test_resolver(root: Path) -> None:
    path, entry = figure_refs.resolve("kelani-2025-bui", root)
    check("a listed figure resolves to its PNG",
          path.name == "bui.png" and entry["caption"].startswith("BUI"))
    path, _ = figure_refs.resolve("kelani-2025-ndvi_class", root)
    check("an underscore belongs to the figure name", path.name == "ndvi_class.png")
    check("upper case is read as lower case",
          figure_refs.resolve("Kelani-2025-BUI", root)[0].name == "bui.png")

    check("a PNG not in index.json is not served",
          refused("kelani-2025-unlisted", root))
    check("a listed figure with no PNG is a 404, not a crash",
          refused("kelani-2025-ghost", root))
    check("an unknown experiment is refused", refused("derna-2023-bui", root))
    check("a name that is not region-year-figure is refused",
          refused("kelani-2025-west-bui", root))
    for bad in ("kelani-2025-..", "kelani-2025-../x", "../../etc/passwd",
                "kelani-2025-bui.png", "kelani-2025-", ""):
        check(f"refused: {bad!r}", refused(bad, root))


def test_prompt(root: Path) -> None:
    block = figure_refs.prompt_block("kelani-2025", root)
    check("the block names each listed figure by its exact placeholder",
          "`<image - kelani-2025-bui>`" in block
          and "`<image - kelani-2025-ndvi_class>`" in block)
    check("an unlisted PNG never reaches the prompt", "unlisted" not in block)
    check("no figures, no section", figure_refs.prompt_block("derna-2023", root) == "")
    check("an experiment that is not region-year gets no section",
          figure_refs.prompt_block("kelani-2025-west", root) == "")
    check("an experiment named in the message is found",
          figure_refs.named_experiment("For the experiment kelani-2025 get me the BUI",
                                       root) == "kelani-2025")
    check("a region-year with no figures is not taken",
          figure_refs.named_experiment("the derna-2023 flood", root) == "")
    check("a message naming none gives none",
          figure_refs.named_experiment("what is BUI?", root) == "")
    check("the placeholder pattern finds what Argus writes",
          figure_refs.PLACEHOLDER.findall("See\n<image - kelani-2025-bui>\nhere")
          == ["kelani-2025-bui"])


def test_http(root: Path) -> None:
    original = figure_refs.ROOT
    figure_refs.ROOT = root
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/figure/"
    try:
        with urllib.request.urlopen(base + "kelani-2025-bui") as reply:
            check("GET /api/figure/<name> serves the PNG",
                  reply.status == 200
                  and reply.headers["Content-Type"] == "image/png"
                  and reply.read() == PNG)
            check("the console on another port may read it",
                  reply.headers["Access-Control-Allow-Origin"] == "*")
        try:
            urllib.request.urlopen(base + "kelani-2025-nope")
            check("an unknown figure is a 404", False, "got 200")
        except urllib.error.HTTPError as exc:
            reason = json.loads(exc.read()).get("error", "")
            check("an unknown figure is a 404 that says why",
                  exc.code == 404 and "nope" in reason, reason)
    finally:
        server.shutdown()
        figure_refs.ROOT = original


def test_rename() -> None:
    event = api._to_event("12:00:00  argos  -> citadel  delegate  sewage", 0)
    check("a delegation to citadel reaches the console as chanakya",
          event and event.get("routedTo") == "chanakya"
          and "citadel" not in event["message"], str(event))
    event = api._to_event("12:00:01  citadel  .  finding  done", 1)
    check("a line from citadel is attributed to chanakya",
          event and event["agent"] == "chanakya", str(event))


def write_index_tif(path: Path, scaled_value: int = -5000) -> None:
    """A small int16 index raster, scaled 1e-4 with nodata -32768 -- the shape
    `fetch_lulc_data` writes."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.full((40, 60), scaled_value, dtype="int16")
    data[:10, :10] = -32768                         # a cloud gap
    with rasterio.open(path, "w", driver="GTiff", width=60, height=40, count=1,
                       dtype="int16", crs="EPSG:32644", nodata=-32768,
                       transform=from_origin(370000, 780000, 20, 20)) as dst:
        dst.write(data, 1)
        dst.scales = (0.0001,)


def test_run_rasters(tmp: Path) -> None:
    from core.session import Place, SessionRecord, Window

    outputs = tmp / "outputs"
    index = outputs / "run_figures.json"
    scene = outputs / "S2C_T44NLN_20251225T051420_L2A"
    write_index_tif(scene / "bui.tif")
    write_index_tif(scene / "ndvi_class.tif", 2)
    write_index_tif(outputs / "terrain" / "elevation_46afa34b80ac.tif", 300)
    outside = tmp / "elsewhere" / "ndvi.tif"
    write_index_tif(outside)

    record = SessionRecord(session_id="r")
    record.place = Place(name="Kelani", display="Kelani River, Sri Lanka")
    record.window = Window(start="2025-01-01", end="2025-12-31")
    scope = figure_refs.scope_for(record)
    check("the scope is the place's first word and the window's year",
          scope == "kelani-2025", scope)
    check("no window, no scope",
          figure_refs.scope_for(SessionRecord(session_id="x")) == "")

    legend = {"1": "water_or_wet", "2": "bare_or_built", "3": "sparse_vegetation",
              "4": "moderate_vegetation", "5": "dense_vegetation"}
    payload = {"result_1": {"task": "fetch_lulc_data", "output": {
        "aoi": {"bbox": [79.8, 6.783, 80.217, 7.083],
                "land": {"excluded_classes": ["ocean"]}},
        "indices": {
            "bui": {"raster": {"path": str(scene / "bui.tif")}},
            "ndvi": {"classes": {
                "legend": legend,
                "fractions": {"water_or_wet": 0.01, "bare_or_built": 0.99},
                "area_km2": {"water_or_wet": 0.5, "bare_or_built": 0.4},
                "raster": {"path": str(scene / "ndvi_class.tif")}}}},
        "elevation": str(outputs / "terrain" / "elevation_46afa34b80ac.tif"),
        "stray": str(outside), "missing": str(scene / "ndvi.tif")}}}
    entries = figure_refs.register_run(payload, scope, outputs, index)
    by_name = {e["name"]: e for e in entries}
    check("index and class rasters the run wrote are registered as run- names",
          sorted(by_name) == ["run-kelani-2025-bui", "run-kelani-2025-elevation",
                              "run-kelani-2025-ndvi_class"], str(sorted(by_name)))
    klass = by_name.get("run-kelani-2025-ndvi_class", {})
    check("a class raster carries its legend, fractions, areas and denominator",
          klass.get("kind") == "classes" and klass.get("legend") == legend
          and klass.get("fractions", {}).get("bare_or_built") == 0.99
          and klass.get("share_of") == "observed land", str(klass)[:120])
    check("each entry keeps the AOI and the excluded sea classes",
          by_name["run-kelani-2025-bui"]["exclude"] == ["ocean"]
          and by_name["run-kelani-2025-bui"]["bbox"][0] == 79.8)
    check("a raster outside outputs/, or one not on disk, is not offered",
          not any("ndvi" == e["figure"] for e in entries))
    bare = figure_refs.register_run(
        {"r": {"output": {"x": str(scene / "ndvi_class.tif")}}}, "other-2025",
        outputs, outputs / "other.json")
    check("a class raster WITHOUT its legend is not offered", bare == [])
    check("a bad scope registers nothing",
          figure_refs.register_run(payload, "Kelani 2025", outputs, index) == [])

    block = figure_refs.prompt_block("", run=entries)
    check("Argus is told these are THIS run's rasters, by placeholder",
          "THIS run" in block and "`<image - run-kelani-2025-bui>`" in block)

    # The Water Body Mask is a network read; stand it in with a grid whose
    # left quarter is ocean (code 1), on whatever grid the caller asks for.
    import numpy as np
    from core.workflows import landmask
    calls = []

    def fake_codes(bbox, plan):
        calls.append(plan.shape)
        codes = np.zeros(plan.shape, dtype="uint8")
        codes[:, : plan.shape[1] // 4] = 1
        return codes

    original_codes = landmask.water_codes
    landmask.water_codes = fake_codes
    try:
        thread = figure_refs.prerender(entries, outputs)
        thread.join(timeout=120)
        check("the background render draws every figure when the run ends",
              all((Path(e["tif"]).with_suffix(".jpg")).is_file() for e in entries))
        jpg, entry = figure_refs.resolve_run("run-kelani-2025-bui", outputs, index)
        check("a run name resolves to a JPEG beside the GeoTIFF",
              jpg.suffix == ".jpg" and jpg.parent == scene
              and jpg.read_bytes()[:3] == b"\xff\xd8\xff", str(jpg))
        check("the intermediate PNG is cleaned up",
              not (scene / "bui.render.png").exists())
        first = jpg.stat().st_mtime
        figure_refs.resolve_run("run-kelani-2025-bui", outputs, index)
        check("a second request reuses the JPEG", jpg.stat().st_mtime == first)
        # Three figures in two folders (the scene, and terrain/): one build
        # per folder, shared by bui and ndvi_class in the scene.
        check("the sea mask is built once per grid, not once per figure",
              len(calls) == 2 and len(list(scene.glob("ocean_*.npy"))) == 1,
              str(calls))
        mask = np.load(next(scene.glob("ocean_*.npy")))
        check("the cached sea mask sits on the raster's own grid",
              mask.shape == (40, 60) and mask[:, :15].all() and not mask[:, 15:].any())
        cls_jpg, _ = figure_refs.resolve_run("run-kelani-2025-ndvi_class", outputs, index)
        check("the class map renders with its legend", cls_jpg.is_file())

        # A sea mask on the wrong grid would shade the wrong ground; the
        # renderer refuses it, and that refusal reaches the caller as a reason.
        landmask.water_codes = lambda bbox, plan: np.zeros((3, 3), dtype="uint8")
        for stale in scene.glob("ocean_*.npy"):
            stale.unlink()
        (scene / "bui.jpg").unlink()
        try:
            figure_refs.resolve_run("run-kelani-2025-bui", outputs, index)
            check("a sea mask on the wrong grid is refused, not drawn", False, "drawn")
        except LookupError as exc:
            check("a sea mask on the wrong grid is refused, not drawn",
                  "ocean mask" in str(exc), str(exc)[:80])

        # No mask service at all: drawn anyway, and the figure says so.
        def down(bbox, plan):
            raise OSError("no route to the mask bucket")
        landmask.water_codes = down
        for stale in scene.glob("ocean_*.npy"):
            stale.unlink()
        jpg, _ = figure_refs.resolve_run("run-kelani-2025-bui", outputs, index)
        check("an unreachable mask service still yields the figure", jpg.is_file())
    finally:
        landmask.water_codes = original_codes

    for bad in ("run-kelani-2025-nope", "run-kelani-bui", "run-../../x",
                "run-kelani-2025-bui.tif"):
        try:
            figure_refs.resolve_run(bad, outputs, index)
            check(f"refused: {bad!r}", False, "resolved")
        except LookupError:
            check(f"refused: {bad!r}", True)

    # The registry pointing outside outputs/ -- edited by hand, or stale -- is
    # refused at serve time too, not only at registration.
    known = json.loads(index.read_text(encoding="utf-8"))
    known["run-kelani-2025-ndvi"] = {**known["run-kelani-2025-bui"],
                                     "name": "run-kelani-2025-ndvi",
                                     "figure": "ndvi", "tif": str(outside)}
    index.write_text(json.dumps(known), encoding="utf-8")
    try:
        figure_refs.resolve_run("run-kelani-2025-ndvi", outputs, index)
        check("a registry entry outside outputs/ is refused", False, "served")
    except LookupError:
        check("a registry entry outside outputs/ is refused", True)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        test_resolver(root)
        test_prompt(root)
        test_http(root)
    with tempfile.TemporaryDirectory() as tmp:
        test_run_rasters(Path(tmp))
    test_rename()

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
