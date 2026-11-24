"""Step 1: read the reference flood map and rasterise it.

The UNOSAT delivery is a zip of shapefiles in EPSG:4326. They are read here
with a small reader over the shapefile format itself, straight out of the zip,
so the repo needs no vector stack (fiona, shapely, geopandas) for one file
type -- the repo keeps the dependency list to rasterio and numpy on purpose.

**Holes are honoured by winding count.** A dissolved flood layer is one
multipolygon of thousands of rings: outer rings run clockwise, holes
counter-clockwise (the shapefile rule). Every ring is burned with
`MergeAlg.add`, outers as +1 and holes as -1; a pixel is inside when the sum
is positive. That handles holes, and islands inside holes, without having to
work out which hole belongs to which outer ring.

Only rings whose bounding box touches the AOI are reprojected and burned: the
Sri Lanka layer is 140 MB, almost all of it outside the Kelani box.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

POLYGON_TYPES = {5, 15, 25}          # Polygon, PolygonZ, PolygonM


def _member(archive: zipfile.ZipFile, layer: str, suffix: str) -> str:
    names = [n for n in archive.namelist()
             if Path(n).name.lower() == f"{layer}{suffix}".lower()]
    if not names:
        raise FileNotFoundError(f"{layer}{suffix} is not in the reference zip")
    return names[0]


def read_dbf(data: bytes) -> list[dict[str, str]]:
    """The attribute table of a shapefile (dBASE III), one dict per record, in
    record order -- which is the order of the shapes in the .shp.

    Values are stripped strings; nothing is typed, because the only use is
    matching a feature's class name against the config (`where`). A deleted
    record ("*") is kept as an empty dict so the order still lines up.
    """
    n, header_len, record_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("ascii", "replace")
        fields.append((name, data[pos + 16]))
        pos += 32
    rows = []
    for i in range(n):
        rec = data[header_len + i * record_len: header_len + (i + 1) * record_len]
        if rec[:1] == b"*":
            rows.append({})
            continue
        row, cursor = {}, 1
        for name, width in fields:
            row[name] = rec[cursor:cursor + width].decode("utf-8", "replace").strip()
            cursor += width
        rows.append(row)
    return rows


def _keep(row: dict[str, str], where: dict[str, list[str]] | None) -> bool:
    """Every named field holds one of its listed values. Field names may be
    given in full ("notation"); dBASE cuts them at 10 characters."""
    for field, allowed in (where or {}).items():
        value = row.get(field, row.get(field[:10]))
        if value is None:
            raise ValueError(f"the reference layer has no field {field!r}; it has "
                             f"{sorted(row)}")
        if value not in [str(a) for a in allowed]:
            return False
    return True


def read_rings(zip_path: str | Path, layer: str,
               bbox: tuple[float, float, float, float] | None = None,
               where: dict[str, list[str]] | None = None,
               ) -> tuple[list[np.ndarray], list[bool], str]:
    """`(rings, is_hole, prj_wkt)` of every polygon ring in a zipped layer.

    `bbox` (west, south, east, north, in the layer's CRS) keeps only rings
    whose own bounding box touches it. `where` ({field: [values]}) keeps only
    features whose attributes match -- a Copernicus EMS observed-event layer
    holds flooded area, flood trace and a landslide in one file.
    """
    with zipfile.ZipFile(zip_path) as archive:
        shp = archive.read(_member(archive, layer, ".shp"))
        prj = archive.read(_member(archive, layer, ".prj")).decode("utf-8", "replace")
        rows = read_dbf(archive.read(_member(archive, layer, ".dbf"))) if where else None

    if struct.unpack(">i", shp[:4])[0] != 9994:
        raise ValueError(f"{layer}.shp is not a shapefile (bad file code)")
    shape_type = struct.unpack("<i", shp[32:36])[0]
    if shape_type not in POLYGON_TYPES:
        raise ValueError(f"{layer} has shape type {shape_type}; only polygons "
                         "can be a flood map")

    rings: list[np.ndarray] = []
    holes: list[bool] = []
    offset, index = 100, -1
    while offset + 8 <= len(shp):
        _, length_words = struct.unpack(">ii", shp[offset:offset + 8])
        content = offset + 8
        offset = content + 2 * length_words
        index += 1
        record_type = struct.unpack("<i", shp[content:content + 4])[0]
        if record_type == 0:                                  # null shape
            continue
        if rows is not None and not _keep(rows[index] if index < len(rows) else {}, where):
            continue
        n_parts, n_points = struct.unpack("<ii", shp[content + 36:content + 44])
        parts = np.frombuffer(shp, "<i4", n_parts, content + 44)
        points = np.frombuffer(shp, "<f8", 2 * n_points,
                               content + 44 + 4 * n_parts).reshape(-1, 2)
        bounds = list(parts) + [n_points]
        for start, stop in zip(bounds[:-1], bounds[1:]):
            ring = points[start:stop]
            if len(ring) < 4:
                continue
            if bbox is not None:
                west, south = ring.min(axis=0)
                east, north = ring.max(axis=0)
                if east < bbox[0] or west > bbox[2] or north < bbox[1] or south > bbox[3]:
                    continue
            x, y = ring[:, 0], ring[:, 1]
            signed = 0.5 * float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))
            rings.append(ring)
            holes.append(signed > 0)          # counter-clockwise = hole
    return rings, holes, prj


def grid_for(bbox: list[float], crs: str, resolution: float) -> dict[str, Any]:
    """A north-up grid in `crs` covering the WGS84 `bbox`, snapped to `resolution`."""
    import math

    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds

    west, south, east, north = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
    west = math.floor(west / resolution) * resolution
    north = math.ceil(north / resolution) * resolution
    width = int(math.ceil((east - west) / resolution))
    height = int(math.ceil((north - south) / resolution))
    return {"crs": crs, "transform": from_origin(west, north, resolution, resolution),
            "width": width, "height": height, "resolution": resolution}


def burn(rings: list[np.ndarray], holes: list[bool], src_crs: str,
         grid: dict[str, Any]) -> np.ndarray:
    """Rings -> a boolean raster on `grid`, holes cut out by winding count."""
    from rasterio.enums import MergeAlg
    from rasterio.features import rasterize
    from rasterio.warp import transform

    shape = (grid["height"], grid["width"])
    if not rings:
        return np.zeros(shape, dtype=bool)
    lengths = [len(r) for r in rings]
    xs, ys = transform(src_crs, grid["crs"],
                       np.concatenate([r[:, 0] for r in rings]).tolist(),
                       np.concatenate([r[:, 1] for r in rings]).tolist())
    xs, ys = np.asarray(xs), np.asarray(ys)
    shapes, cursor = [], 0
    for length, hole in zip(lengths, holes):
        coords = np.column_stack([xs[cursor:cursor + length], ys[cursor:cursor + length]])
        cursor += length
        shapes.append(({"type": "Polygon", "coordinates": [coords.tolist()]},
                       -1 if hole else 1))
    count = rasterize(shapes, out_shape=shape, transform=grid["transform"],
                      fill=0, dtype="int32", merge_alg=MergeAlg.add)
    return count > 0


def ingest(cfg: dict[str, Any], grid: dict[str, Any]) -> dict[str, Any]:
    """The reference flood layer on the evaluation grid, plus what was read.

    Returns the flood mask and a description of the layer: what the plan's
    step 1 says to print (geometry type, CRS, extent) is returned instead, so
    it reaches the payload and Entro rather than a console nobody reads.
    """
    from .config import resolve

    ref = cfg["reference"]
    zip_path = resolve(cfg, ref["zip"])
    if zip_path is None or not zip_path.is_file():
        raise FileNotFoundError(f"reference.zip not found: {zip_path}")
    margin = 0.01
    west, south, east, north = cfg["aoi"]["bbox"]
    window = (west - margin, south - margin, east + margin, north + margin)

    flood_where = ref.get("flood_where") or None
    rings, holes, prj = read_rings(zip_path, ref["flood_layer"], window, flood_where)
    if "WGS_1984" not in prj and "WGS 84" not in prj:
        raise ValueError(f"{ref['flood_layer']} is not in WGS84 ({prj[:80]}); "
                         "the reader assumes EPSG:4326")
    flood = burn(rings, holes, "EPSG:4326", grid)

    # An exclusion is a layer name, or {layer, where} to take only some of its
    # features (EMS: the landslide polygon in the observed-event layer is
    # neither flood nor dry -- it is taken out of the domain).
    excluded = np.zeros_like(flood)
    excluded_layers = []
    for item in ref.get("exclude_layers") or []:
        layer, where = (item, None) if isinstance(item, str) else (item["layer"], item.get("where"))
        r, h, _ = read_rings(zip_path, layer, window, where)
        mask = burn(r, h, "EPSG:4326", grid)
        excluded |= mask
        excluded_layers.append({"layer": layer, **({"where": where} if where else {}),
                                "rings_in_aoi": len(r), "pixels": int(mask.sum())})

    # Where the reference LOOKED. Outside it, "no flood mapped" is "not
    # looked at", so it leaves the domain (domain.build). None when the
    # reference ships no such layer (UNOSAT FL20251128LKA).
    analysed = None
    analysed_layer = ref.get("analysed_extent_layer")
    if analysed_layer:
        r, h, _ = read_rings(zip_path, analysed_layer, window)
        analysed = burn(r, h, "EPSG:4326", grid)
        if not analysed.any():
            raise ValueError(f"the reference's analysed extent ({analysed_layer}) does not "
                             "touch the AOI; there is nothing to compare")
    return {
        "flood": flood,
        "excluded": excluded,
        "analysed": analysed,
        "meta": {
            "code": ref["code"], "zip": str(zip_path), "layer": ref["flood_layer"],
            **({"flood_where": flood_where} if flood_where else {}),
            "crs": "EPSG:4326", "geometry": "polygon",
            "rings_in_aoi": len(rings), "holes_in_aoi": int(sum(holes)),
            "flood_pixels_in_grid": int(flood.sum()),
            "excluded_layers": excluded_layers,
            "analysed_extent_layer": analysed_layer,
            "analysed_pixels_in_grid": int(analysed.sum()) if analysed is not None else None,
            "image": ref.get("image") or {},
            "credit": ref.get("credit", ""),
            "description": ref.get("description", ""),
        },
    }
