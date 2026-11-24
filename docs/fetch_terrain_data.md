# Workflow: `fetch_terrain_data`

Elevation and slope for a bounding box, from the Copernicus DEM GLO-30 archive.

Implemented in [`core/workflows/terrain.py`](../core/workflows/terrain.py).
Data source and citations: [SOURCES.md](SOURCES.md).

## Why this is not part of `fetch_lulc_data`

**Sentinel-2 cannot produce slope.** It is a multispectral imager with no stereo
pair and no elevation product; there is no processing chain from reflectance to
terrain. Slope needs a DEM, and a DEM is a different source with different
properties:

- Terrain does not change between flood events, so a result is cacheable
  indefinitely. Nothing optical is.
- Bundling it into the Sentinel-2 workflow would mean a cloudy season takes
  slope down with it. Two sources, two failure modes, two functions.

GLO-30 is used rather than NASADEM or SRTM because those are 2000-vintage and
have voids in steep terrain -- the worst case for a mountainous AOI, which is
the case slope is being computed for.

## Request

```json
{"event_id": 202,
 "tasks": [
   {"function": "fetch_terrain_data",
    "params": {"bbox": [91.80, 25.50, 91.95, 25.62], "event_id": 202}}
 ]}
```

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `bbox` | list[float] | yes | | `[west, south, east, north]`, WGS84 degrees. |
| `output_dir` | str | no | `outputs` | Rasters go to `<output_dir>/<event_id>/`. |
| `event_id` | int/str | no | `terrain` | Subdirectory name. |
| `write_rasters` | bool | no | `true` | `false` computes statistics without writing files. |
| `use_cache` | bool | no | `true` | Reuse an identical previous run. |

The cache key is the bbox alone, which is sound because GLO-30 is a fixed
archive -- the same bbox has the same answer forever. Slope and elevation are
cached together, so a warm run returns the same keys as a cold one.

## Slope

Horn's (1981) 3x3 weighted-gradient method -- the same one behind GDAL's and
ArcGIS' slope.

GLO-30 is stored in EPSG:4326, so the grid spacing is **angular** and has to be
converted to metres before a gradient means anything: a degree of longitude is
111 km at the equator and 100 km at 25°N. This is done with a `cos(latitude)`
factor rather than a reprojection, which keeps the dependency list at rasterio
+ numpy. The residual error over a flood-sized AOI is far below the DEM's own
vertical accuracy.

Because the grid is geographic, **pixel area cannot be `resolution²`** -- a
squared degree is not an area. `GridPlan` carries an explicit metric pixel area
computed at the AOI's latitude, and raises rather than guessing if it is
missing.

A 3x3 kernel cannot be evaluated on the outermost ring, so the AOI is read with
one pixel of padding and the ring is returned as NaN. That is the only reason
`coverage.valid_fraction` is 0.9917 rather than 1.0 in the example below.

| Class | Slope |
|---|---|
| `flat` | < 2° |
| `gentle` | 2-5° |
| `moderate` | 5-15° |
| `steep` | 15-30° |
| `very_steep` | >= 30° |

Slope is computed by Horn's (1981) method; these breaks are applied to it.
The ranking's "steep" share (L2) is `steep` + `very_steep`, slope >= 15°.
From 19 to 21 Nov 2026 the classes were 5° bands after Ozdemir (2009) with
steep >= 30°; the user reverted that on 2026-11-21. Results stored in those
days carry `deg_*` bands, and the ranking reads the bands from 15° up as the
same >= 15° share.

## Response

Live run over Shillong:

```
grid   : [434, 542]  EPSG:4326  0.000277 deg
elev   : min 984.68  max 1962.31  mean 1587.87  (m)
slope  : min 0.0  max 58.11  mean 14.02  median 11.64  p90 28.38
         coverage.valid_fraction 0.9917, saturated_pixels null
classes: flat 3.7%  gentle 11.4%  moderate 48.4%  steep 28.0%  very_steep 8.5%
areas  : flat 7.44  gentle 22.90  moderate 97.41  steep 56.34  very_steep 17.14  (km^2)
```

Cross-checks that held: mean elevation 1588 m against Open-Meteo's independently
resolved 1495 m for the Shillong grid cell, and AOI area 201.2 km² against the
201.06 km² `fetch_permanent_water` computes from a different dataset on a
different grid.

Rasters written: `slope_<key>.tif` and `elevation_<key>.tif` (float32, nodata
-9999) and `slope_class.tif` (uint8).

Saturation is not counted for slope or elevation, so `saturated_pixels` is
null on both: flat ground genuinely is 0 degrees, and elevation's range is
derived from the data, so its extremes define the bound by construction.

## Limits

- **One 1-degree tile.** An AOI spanning a tile boundary raises rather than
  returning half an AOI; mosaicking is not implemented.
- **GLO-30 is a surface model, not a terrain model.** It includes canopy and
  buildings, so slope over dense forest is partly canopy roughness. It reads
  high in exactly the vegetated terrain a flood catchment tends to have.
- **Slope is not the whole terrain story for flooding.** HAND (Height Above
  Nearest Drainage) or TWI from the same DEM speak more directly to where water
  goes. Not built; not ruled out.
