# map_hazard_exposure

The hazard-and-exposure map of Modules 9 and 10 (user decision 2026-11-02).

```
map_hazard_exposure(bbox, flood_raster, flood_label="", hazards=None, maps=None,
                    output_dir="outputs", event_id="", figure_dir="",
                    aoi_fraction=0.75, write_figure=True)
```

| Parameter | Meaning |
|---|---|
| `bbox` | `[west, south, east, north]`, EPSG:4326 |
| `flood_raster` | the stored `get_flood_map` extent (`raster.path`); refused when it is not a file |
| `flood_label` | the product's label, for the legend and title |
| `hazards` | hazard layers from `HAZARD_LAYERS`; default `["flood_extent"]` |
| `maps` | figure name -> exposure layers; default `essential_services` = hospitals, drinking_water, food; `health` = hospitals |

## What it does

1. Locates the facilities with one Overpass request (`osm_context.counts`,
   facilities group only, up to `LOCATE_MAX` = 5,000 per category; where_am_i
   keeps 400). A category that hits the cap is said on the map and in the
   uncertainty block.
2. Tests every located facility against every hazard: `in`, `out`, or
   `not_observed` -- ground the product did not observe is never "out".
3. Groups food shops and markets into counted clusters: the densest point
   seeds a disc of radius 1 km that takes every unassigned outlet within it;
   repeat. No cluster is wider than 2 km.
4. Draws one figure per entry of `maps` on the OpenStreetMap basemap: the
   flood as a **red dotted hatch** over a faint red wash, unobserved land
   grey, the minor-flood pins of the flood overlay, hospitals (blue cross) and
   drinking water (teal diamond) as markers, food clusters as semi-transparent
   amber discs with the count in the middle. Red is kept for hazards.

## Adding a hazard

One entry in `HAZARD_LAYERS`: a `HazardLayer(label, source, draw, test)`,
where `draw(ctx)` returns the layer's RGBA pixels on the map and its legend
entries, and `test(ctx, lons, lats)` returns `in` / `out` / `not_observed`
per point. The drawing, the counts and the modules need no change.

## Output

`exposure.<layer>`: label, mapped (OSM total), located, cap_reached,
not_located, and `in_hazard.<hazard>` = in / out / not_observed / names_in;
point layers keep their points. `food_clusters`: lon, lat, radius_m, count,
and per hazard the outlets in it. `maps.<name>.png`, `figure` (the first, for
the console), `uncertainty`, `sources`.

## Uncertainty

OpenStreetMap holds mapped facilities, not every one; a facility is tested at
one point, so a large site part-flooded may read as dry; clusters are a
display and counting rule, not service areas.
