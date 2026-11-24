# get_pop_data

Population inside a bounding box, from WorldPop Global2 (user decision,
2026-10-30). It replaces `fetch_population` (census / HDX COD-PS / World Bank),
which is retired: a call by that name fails as `unknown_function` and names
this tool.

```
get_pop_data(bbox, country_iso3, year=2025, extent_raster="",
             output_dir="outputs", event_id="", write_figure=True)
```

| Parameter | Meaning |
|---|---|
| `bbox` | `[west, south, east, north]`, degrees, EPSG:4326 |
| `country_iso3` | the country whose raster is read; one country per call |
| `year` | 2015-2030; the Flood bot pipeline passes the event's start year |
| `extent_raster` | optional: `get_flood_map`'s `raster.path`. Adds `flood_extent`: the people inside the mapped flood extent ("population affected", Modules 9 and 10) and, apart, the people on land the product did not observe |

## What it does

1. Looks the file up on the WorldPop hub:
   `https://hub.worldpop.org/rest/data/pop/G2_CN_POP_R25A_100m?iso3=<ISO3>`,
   the row whose `popyear` is `year`, its `files[0]`.
2. Downloads the country GeoTIFF once into `data/cache/worldpop_g2/` with a
   sidecar (url, retrieval time, SHA-256). An uncached run downloads it again.
   Sri Lanka 2025 is 17.64 MB.
3. Reads only the window of pixels whose **centre** lies inside the box, and
   sums them. No-data (-99999) and negative pixels count as zero people.
4. Reads the one-pixel ring around that window, and the window's own edge, to
   give `range`: total less its edge pixels, total plus the ring.

The sea needs no mask: WorldPop places nobody there.

## Returns

| Key | Meaning |
|---|---|
| `population` | people in the box, rounded |
| `range` | `[low, high]` from the edge band |
| `vintage` | `WorldPop Global2 R2025A v1, <year>` |
| `dataset` | id, release, `constrained: true`, resolution, units |
| `pixels` | `counted`, `with_people`, `no_data` |
| `coverage.bbox_in_raster` | share of the box the country raster covers; below 1 the rest is not counted |
| `uncertainty`, `sources`, `run` | the tool contract |

Stored as `inputs/population.json` (`CACHE_NAMES`), which Modules 11-12 read
(`report_render.f_people_aoi`, `module_blocks.b_people_in_aoi`). A
`population.json` stored by the retired census tool is not re-read: the
reports say NOT KNOWN and name `get_pop_data`.

## Standing uncertainty

Registered with the tool (`@tool(..., data_source_uncertainty=...,
method_uncertainty=...)`), and attached to every
stored result as `uncertainty.standing`.

- **Data source.** WorldPop Global2 R2025A is a modelled estimate, not a
  count: census totals are spread over settlement pixels by a random-forest
  model, and WorldPop labels this release alpha. Being constrained, it places
  people only where settlement is mapped, so missed settlements push people
  into neighbours.
- **Method.** The total sums the ~100 m pixels whose centre falls inside the
  bounding box, so the edge pixels can move it by the reported range. Only the
  named country's raster is read; any part of the box outside it is not
  counted.
- **Model.** Todo: run the same event 10 times uncached (the RQ5 N = 10 batch)
  and compare the runs.

## Checked

`tests/test_pop_data.py`: a synthetic 10 x 10 raster (exact sum, range,
no-data, a box half outside, refusals, the report readers). `--live` reads the
real Sri Lanka 2025 file: the kelani-lower-2025 AOI
`[79.94, 6.905, 80.09, 6.978]` holds **273,819** people (range 261,821 to
285,707), 15,840 pixels counted, 15,428 with people, 207 no-data (dated
2026-10-30, WorldPop Global2 R2025A v1, 2025).

## Todo

- People inside the **flood extent** (the WorldPop pixels under the flood
  mask), and that as a **share of the national population** (World Bank
  `SP.POP.TOTL`, kept as national context). `f_people_extent` says NOT KNOWN
  until then.

Source entry: [SOURCES.md](SOURCES.md), section 8f, "Population".

## People in the flood (since 2026-11-02)

With `extent_raster`, the extent's codes (1 flooded, 255 not observed) are
resampled onto the counted WorldPop window by AVERAGE, giving the share of
each WorldPop pixel that is flooded and the share not observed. Each pixel's
people are multiplied by those shares and summed:

- `flood_extent.people_in_flood` -- the population affected;
- `flood_extent.people_not_observed` -- on land the product did not observe,
  never counted as unaffected.

Shares rather than a centre test, because WorldPop's ~100 m pixels and the
flood products' 20-375 m pixels never align: a pixel half under water is half
counted. The count is tied to the extent it was made from
(`flood_extent.extent_raster`); a module describing another extent says the
count is for another extent rather than reuse it.

## The population map (since 2026-11-18)

With `write_figure` (the default) the tool draws `population_map.png` under
`<output_dir>/<event_id>/` and returns it as `figure` (name, path, caption),
so the console shows it as a `run-` figure and Modules 9-10 place it with
`{{figure:hev_population_map}}`. Drawn from the same window the total is
summed over: people per pixel on a log scale (settlement density spans four
orders of magnitude). With `extent_raster`, flooded pixels are red and land
mostly unobserved is outlined and lightly hatched -- never filled, because
over Kandy 94 % of the land was unobserved and a filled overlay hid the
people. The legend gives no share: one counted on the WorldPop grid would
disagree with the module's share of the land. A drawing failure is reported
under `map` and never fails the count.

Modules 9-10 now run the tool on a "no flood mapped" extent too
(`flood_analyst.observed_extent`): the AOI total and the people on land the
product did not observe are measured, and "population affected" is written
as not known, never 0.

