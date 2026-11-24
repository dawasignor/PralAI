"""Figure placeholders: how an answer refers to a cached raster figure.

Argus writes `<image - kelani-2025-bui>` in an answer; the console finds the
placeholder and asks `GET /api/figure/kelani-2025-bui` for the PNG. The text
reaches the reader at once and each image loads beside it.

The name is `region-year-figure`. The experiment is `region-year` and the rest
is the figure, so `kelani-2025-ndvi_class` is figure `ndvi_class` of experiment
`kelani-2025`. An experiment named any other way has no placeholder.

**Only a figure listed in the experiment's `figures/index.json` resolves.**
The index is what the figure step wrote, with a caption and a provenance per
entry; a PNG that is not in it has neither, and is not served. Argus is given
that same list (`prompt_block`) so it never has to guess a name.

**A raster the run itself just wrote is `run-region-year-figure`** (since
2026-10-10). A live `fetch_lulc_data` writes GeoTIFFs under `outputs/`, which no
browser can show, and which are a different scene and often a different AOI
from the experiment's figures -- so they get their own prefix and can never be
mistaken for the experiment's. `register_run` records them in
`outputs/run_figures.json`; `resolve` renders the GeoTIFF on first request with
the thesis renderer (`core/figures.render`) and serves it as a JPEG, cached
beside the TIF until the TIF changes.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .experiment import ROOT

PLACEHOLDER = re.compile(r"<image\s*-\s*([a-z0-9_-]+)\s*>")

# region-year-figure. The region is one lowercase word, the year four digits.
NAME = re.compile(r"^(?P<experiment>[a-z]+-\d{4})-(?P<figure>[a-z0-9][a-z0-9_]{0,63})$")

# run-region-year-figure: a raster the latest run wrote, not an experiment's.
RUN_NAME = re.compile(r"^run-(?P<scope>[a-z]+-\d{4})-(?P<figure>[a-z0-9][a-z0-9_]{0,63})$")
OUTPUTS = ROOT.parent / "outputs"
RUN_INDEX = OUTPUTS / "run_figures.json"

# Matplotlib's pyplot is not thread-safe and the console server is threaded:
# two images requested at once would draw into each other's figure.
_RENDER_LOCK = threading.Lock()


def _served(path: Path, outputs: Path) -> bool:
    """Is `path` inside a folder run figures may come from?

    `outputs/`, and since 2026-10-15 the session folders, where a console run's
    workflows now write (core/session_store.py). Nothing else: a name resolves
    to a file only inside these.
    """
    from .session import SESSIONS_DIR

    roots = (outputs.resolve(), SESSIONS_DIR.resolve())
    return any(root in path.parents for root in roots)


def placeholder(experiment: str, figure: str) -> str:
    return f"<image - {experiment}-{figure}>"


def _index(experiment: str, root: Path | None = None) -> list[dict[str, Any]]:
    """The experiment's figure manifest, or [] when it has none."""
    path = (root or ROOT) / experiment / "figures" / "index.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("name")]


def list_figures(experiment: str, root: Path | None = None) -> list[dict[str, Any]]:
    """Figures a placeholder can name for this experiment, with captions.

    Empty when the experiment name is not `region-year`: such an experiment
    has figures, but no placeholder can address them.
    """
    if not re.fullmatch(r"[a-z]+-\d{4}", experiment or ""):
        return []
    return [
        {"name": e["name"],
         "placeholder": placeholder(experiment, e["name"]),
         "caption": e.get("caption") or ""}
        for e in _index(experiment, root)
        if NAME.match(f"{experiment}-{e['name']}")
    ]


def resolve(name: str, root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """`kelani-2025-bui` -> the PNG on disk and its index entry.

    Raises LookupError with a reason a reader can act on. The path is built
    from the two validated halves, never from the raw request, and must sit
    inside the experiment's own figures folder.

    A `run-` name is dispatched to `resolve_run`, which returns a JPEG.
    """
    if (name or "").strip().lower().startswith("run-"):
        return resolve_run(name)
    match = NAME.match((name or "").strip().lower())
    if not match:
        raise LookupError(
            f"{name!r} is not a figure name: expected region-year-figure, "
            "for example kelani-2025-bui")
    experiment, figure = match["experiment"], match["figure"]

    entry = next((e for e in _index(experiment, root) if e["name"] == figure), None)
    if entry is None:
        raise LookupError(
            f"experiment {experiment!r} has no figure {figure!r} in its "
            "figures/index.json")

    folder = ((root or ROOT) / experiment / "figures").resolve()
    path = (folder / f"{figure}.png").resolve()
    if path.parent != folder or not path.is_file():
        raise LookupError(f"figure {figure!r} of {experiment!r} is listed but "
                          "its PNG is missing")
    return path, entry


def named_experiment(text: str, root: Path | None = None) -> str:
    """The first `region-year` in a message that is an experiment with figures.

    The console sends no `experiment` field, so "for the experiment kelani-2025
    get me the BUI" reached Argus with no figure list and could not be shown
    one. Used for FIGURES ONLY: writing to an experiment's cache stays opt-in
    through the request's own `experiment` field.
    """
    for slug in re.findall(r"\b[a-z]+-\d{4}\b", (text or "").lower()):
        if _index(slug, root):
            return slug
    return ""


def prompt_block(experiment: str, root: Path | None = None,
                 run: list[dict[str, Any]] | None = None) -> str:
    """The figures Argus may place in an answer, as prompt text.

    Two groups: the rasters THIS run wrote (`run`), then the experiment's
    cached figures. They are labelled apart because they are usually different
    scenes and areas, and an answer about this run's numbers must show this
    run's raster.

    Empty when there are none, so the section disappears rather than telling
    Argus about a list with nothing in it.
    """
    lines = []
    if run:
        lines.append("Rasters THIS run produced -- show these when the answer "
                     "is about this run's results:")
        lines += [f"- `{f['placeholder']}` -- {f['caption']}".rstrip(" -")
                  for f in run]
    figures = list_figures(experiment, root)
    if figures:
        lines.append(f"Cached figures of experiment `{experiment}` -- a "
                     "different run; say so if you show one:")
        lines += [f"- `{f['placeholder']}` -- {f['caption']}".rstrip(" -")
                  for f in figures]
    if not lines:
        return ""
    return ("## Figures you may show\n"
            "To show one, put its placeholder on a line of its own in `answer`, "
            "exactly as written here. Never write a placeholder that is not in "
            "this list, and never write a file path instead.\n"
            + "\n".join(lines) + "\n\n")


# ---------------------------------------------------------------------------
# Rasters a live run wrote: run-region-year-figure
# ---------------------------------------------------------------------------
def scope_for(record) -> str:
    """`region-year` for a run's rasters: the first word of the place the user
    named, and the year the window starts. "" when either is missing -- a
    raster with no name a reader can recognise is not offered."""
    if record is None:
        return ""
    words = re.findall(r"[a-z]+", (record.place.name or record.place.display or "").lower())
    year = (record.window.start or "")[:4]
    return f"{words[0]}-{year}" if words and re.fullmatch(r"\d{4}", year) else ""


def _tifs(value: Any) -> list[str]:
    """Every string in a payload that names a .tif, however deep."""
    if isinstance(value, str):
        return [value] if value.lower().endswith(".tif") else []
    if isinstance(value, dict):
        return [t for v in value.values() for t in _tifs(v)]
    if isinstance(value, list):
        return [t for v in value for t in _tifs(v)]
    return []


def _figure_name(tif: Path) -> str:
    # Terrain writes `elevation_46afa34b80ac.tif`: the suffix is a cache key,
    # not part of what the figure is.
    return re.sub(r"_[0-9a-f]{12}$", "", tif.stem.lower())


def _load_runs(index: Path) -> dict[str, Any]:
    try:
        return json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _class_blocks(value: Any) -> dict[str, dict[str, Any]]:
    """Every class summary in a payload, keyed by its raster's figure name.

    A class summary is what `_raster.class_fractions` writes: `legend`,
    `fractions` and `area_km2`, with the class raster beside them under
    `raster`. It is what a class map's legend needs and cannot be derived from
    the raster alone -- a figure that recomputed its own shares could disagree
    with the table the answer quotes.
    """
    found: dict[str, dict[str, Any]] = {}
    if isinstance(value, dict):
        raster = value.get("raster")
        path = raster.get("path") if isinstance(raster, dict) else raster
        if isinstance(value.get("legend"), dict) and isinstance(path, str):
            found[_figure_name(Path(path))] = value
        for child in value.values():
            found.update(_class_blocks(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_class_blocks(child))
    return found


# What a class map's shares are OF. NDVI classes are shares of the observed
# land (cloud is excluded); slope and JRC water have no cloud mask, so theirs
# are shares of the AOI's land. Printed in the legend: a percentage without its
# denominator is the most reliable way to be wrong in this project.
SHARE_OF = {"ndvi_class": "observed land"}


def register_run(results: dict[str, Any], scope: str,
                 outputs: Path | None = None,
                 index: Path | None = None) -> list[dict[str, Any]]:
    """Record the rasters a Monty batch wrote, as `run-` figures.

    Index rasters (`bui`, `ndvi`, `slope`, ...) and class rasters
    (`ndvi_class`, `slope_class`, `water_class`) both. A class raster is
    offered only when its payload carries the legend, fractions and areas --
    drawn without them it would be a class map with no key, and drawn on a
    continuous ramp it would invent categories.

    Each entry also keeps the AOI's bbox and the Water Body Mask classes the
    workflow excluded (`aoi.land.excluded_classes`), so the figure can shade
    the sea instead of counting it as cloud. The newest run under a name
    replaces the older one -- `run-` means "the latest run".
    """
    from .figures import CAPTIONS, CLASS_CAPTIONS, CLASS_COLOURS, RANGES, water_bins

    if not re.fullmatch(r"[a-z]+-\d{4}", scope or ""):
        return []
    outputs = (outputs or OUTPUTS).resolve()
    index = index or RUN_INDEX
    repo = outputs.parent
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    entries: dict[str, dict[str, Any]] = {}
    for result in (results or {}).values():
        output = result.get("output") if isinstance(result, dict) else None
        output = output if isinstance(output, dict) else result
        aoi = (output or {}).get("aoi") if isinstance(output, dict) else None
        aoi = aoi if isinstance(aoi, dict) else {}
        land = aoi.get("land") if isinstance(aoi.get("land"), dict) else {}
        classes = _class_blocks(output)

        # A chart the workflow drew itself (`data_availability`): a PNG with
        # its own caption, served as it is -- nothing to render.
        chart = (output or {}).get("figure") if isinstance(output, dict) else None
        if isinstance(chart, dict) and str(chart.get("path", "")).lower().endswith(".png"):
            path = Path(chart["path"])
            path = (path if path.is_absolute() else repo / path).resolve()
            figure = str(chart.get("name") or _figure_name(path))
            if (_served(path, outputs) and path.is_file()
                    and re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", figure)):
                name = f"run-{scope}-{figure}"
                entries[name] = {
                    "name": name, "placeholder": f"<image - {name}>",
                    "figure": figure, "kind": "chart", "png": str(path),
                    "caption": chart.get("caption", ""), "registered": now,
                }

        for raw in _tifs(output):
            path = Path(raw)
            path = (path if path.is_absolute() else repo / path).resolve()
            if not _served(path, outputs) or not path.is_file():
                continue
            figure = _figure_name(path)
            if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", figure):
                continue
            if figure in RANGES:
                kind, extra = "index", {"caption": CAPTIONS.get(figure, "")}
            elif figure in CLASS_COLOURS and figure in classes:
                block = classes[figure]
                kind, extra = "classes", {
                    "caption": CLASS_CAPTIONS.get(figure, ""),
                    "legend": block["legend"],
                    "fractions": block.get("fractions") or {},
                    "areas": block.get("area_km2") or {},
                    "share_of": SHARE_OF.get(figure, "AOI land"),
                    # JRC bins from the thresholds THIS run used, which a call
                    # may override; NDVI and slope bins are fixed tables.
                    **({"bins": water_bins(
                        float((output.get("thresholds") or {}).get("permanent", 90)),
                        float((output.get("thresholds") or {}).get("seasonal", 25)))}
                       if figure == "water_class" else {}),
                }
            else:
                continue
            name = f"run-{scope}-{figure}"
            entries[name] = {
                "name": name, "placeholder": f"<image - {name}>",
                "figure": figure, "kind": kind, "tif": str(path),
                "bbox": aoi.get("bbox"),
                "exclude": list(land.get("excluded_classes") or []),
                "registered": now, **extra,
            }
    if entries:
        known = _load_runs(index)
        known.update(entries)
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps(known, indent=1), encoding="utf-8")
    return list(entries.values())


def _ocean(tif: Path, bbox, exclude: list[str]) -> tuple[Any, str]:
    """The excluded water on the raster's own grid, or (None, why not).

    Rebuilt from the Copernicus DEM Water Body Mask with the same classes the
    workflow excluded, warped onto the TIF's grid by `landmask.water_codes` --
    the mask the statistics were computed with, so the shaded sea is exactly
    the ground the numbers left out. Cached once per grid beside the TIFs: every
    index of one run shares a grid, and the mask tiles are a network read.
    """
    import hashlib

    import numpy as np
    import rasterio

    if not (bbox and exclude):
        return None, "no sea was excluded"
    from .workflows import _raster as R, landmask

    with rasterio.open(tif) as src:
        crs, transform, shape = src.crs, src.transform, (src.height, src.width)
    key = hashlib.sha1(json.dumps(
        [str(crs), list(transform)[:6], shape, list(bbox), sorted(exclude)]
    ).encode()).hexdigest()[:12]
    cache = tif.parent / f"ocean_{key}.npy"
    if cache.is_file():
        return np.load(cache), ""
    try:
        plan = R.GridPlan(None, transform, crs, shape, abs(transform.a),
                          resolution_y=abs(transform.e))
        codes = landmask.water_codes(list(bbox), plan)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"sea mask unavailable ({type(exc).__name__})"
    drop = [code for code, label in landmask.WBM_CLASSES.items() if label in exclude]
    ocean = np.isin(codes, drop)
    np.save(cache, ocean)
    return ocean, ""


def _ensure_jpg(entry: dict[str, Any], outputs: Path | None = None) -> Path:
    """Render one registered run raster to JPEG, unless a fresh one exists.

    Checked again under the lock, so a request that arrives while the
    background render is drawing the same figure waits for it instead of
    drawing it a second time.
    """
    outputs = (outputs or OUTPUTS).resolve()
    if entry.get("kind") == "chart":
        # Already a finished image: re-encoded as JPEG like every run figure,
        # so the endpoint serves one format for the run- names.
        png = Path(entry["png"]).resolve()
        if not _served(png, outputs) or not png.is_file():
            raise LookupError(f"the chart behind {entry['name']!r} is no longer on disk")
        jpg = png.with_suffix(".jpg")
        if not jpg.is_file() or jpg.stat().st_mtime < png.stat().st_mtime:
            from PIL import Image
            with _RENDER_LOCK, Image.open(png) as image:
                image.convert("RGB").save(jpg, "JPEG", quality=92)
        return jpg
    tif = Path(entry["tif"]).resolve()
    if not _served(tif, outputs) or not tif.is_file():
        raise LookupError(f"the raster behind {entry['name']!r} is no longer on disk")
    jpg = tif.with_suffix(".jpg")

    def fresh() -> bool:
        return jpg.is_file() and jpg.stat().st_mtime >= tif.stat().st_mtime

    if fresh():
        return jpg
    from PIL import Image

    from .figures import render, render_classes

    with _RENDER_LOCK:
        if fresh():
            return jpg
        ocean, ocean_note = _ocean(tif, entry.get("bbox"), entry.get("exclude") or [])
        scope = RUN_NAME.match(entry["name"])["scope"]
        subtitle = f"{scope} -- this run, {tif.parent.name}" + (
            f" -- {ocean_note}" if ocean_note.startswith("sea mask unavailable") else "")
        png = tif.with_suffix(".render.png")
        try:
            if entry.get("kind") == "classes":
                render_classes(tif, png, name=entry["figure"], legend=entry["legend"],
                               fractions=entry.get("fractions"),
                               areas=entry.get("areas"),
                               share_of=entry.get("share_of", "AOI land"),
                               ocean=ocean, subtitle=subtitle, bins=entry.get("bins"))
            else:
                render(tif, png, name=entry["figure"], ocean=ocean, subtitle=subtitle)
        except ValueError as exc:
            # The renderer refuses a raster it would draw misleadingly (a
            # saturated stretch, a mask on the wrong grid). A reason, not a crash.
            raise LookupError(f"{entry['name']}: {exc}") from exc
        with Image.open(png) as image:
            image.convert("RGB").save(jpg, "JPEG", quality=90)
        png.unlink(missing_ok=True)
    return jpg


def prerender(entries: list[dict[str, Any]], outputs: Path | None = None) -> threading.Thread:
    """Draw every registered figure in the background, as soon as the run has
    written them, so they are ready when the answer that names them arrives.
    A figure that fails here is not lost: the endpoint tries again on request
    and reports the reason then."""
    def work() -> None:
        for entry in entries:
            try:
                _ensure_jpg(entry, outputs)
            except Exception:                                  # noqa: BLE001
                continue

    thread = threading.Thread(target=work, daemon=True, name="prerender-figures")
    thread.start()
    return thread


def resolve_run(name: str, outputs: Path | None = None,
                index: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """`run-kelani-2025-bui` -> a JPEG of that run's GeoTIFF.

    Browsers do not display GeoTIFF, and an int16 index scaled by 10000 would
    not mean anything if they did. The thesis renderer draws it -- descaled,
    fixed stretch or a class legend, the excluded sea shaded, scale bar,
    nodata named -- and the PNG is re-encoded as JPEG beside the TIF.
    Re-rendered only when the TIF is newer.
    """
    match = RUN_NAME.match((name or "").strip().lower())
    if not match:
        raise LookupError(f"{name!r} is not a run figure name: expected "
                          "run-region-year-figure, for example run-kelani-2025-bui")
    entry = _load_runs(index or RUN_INDEX).get(match.string)
    if entry is None:
        raise LookupError(f"no run raster is registered as {match.string!r}")
    return _ensure_jpg(entry, outputs), entry
