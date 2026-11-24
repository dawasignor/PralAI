# accuracy_metrics

Agreement of the NRT flood products with a reference flood map, in one table
next to the TOPSIS rank. Built from the user's validation plan
(`validation_plan_unosat.md`, Cyclone Ditwah, Kelani basin).

**Monty computes; Entro interprets.** Monty runs this workflow and produces the
table. Entro reads it through its `accuracy_interpretation` flow
(`agents/entro/workflows/`), in the same run, and explains it from the vault
notes under `Head/Uncertainty/`. The table is never recomputed by an agent.

**Agreement, not accuracy.** The reference is an operational map, not field
truth. Every table, note and sentence says "agreement with the reference".

Code: `core/workflows/accuracy.py` (the registered workflow) and
`core/workflows/validation/` (one module per plan step).
Tests: `tests/test_metrics.py`, `tests/test_accuracy_pipeline.py`,
`tests/test_interpretation_hop.py` (all `pytest`, all also plain scripts).

## Parameters

| Param | Type | Required | Notes |
|---|---|---|---|
| `config` | str | yes | Path to `config.yaml`. For Kelani 2025: `experiments/kelani-2025-west/validation/config.yaml`. |
| `aoi_bbox` | list | no | `[west, south, east, north]` to test a small window first (plan: "test on a small window before the full basin"). Outputs go to `small_window/`. |

Everything else is in the config -- see
[accuracy_metrics.config.example.yaml](accuracy_metrics.config.example.yaml).
A required value left as `TODO` stops the run with the list of what is missing.

## The steps

| Plan step | Module | What it does |
|---|---|---|
| Metrics | `metrics.py` | TP, FP, FN, TN, overall accuracy, precision, recall, F1, IoU, MCC. Undefined is `None`, never 0. |
| 1. Reference | `reference.py` | Reads the shapefile straight from the HDX zip (a small stdlib reader -- no vector stack), rasterises it at 10 m in the config CRS. Holes are cut by winding count. |
| 2. Domain | `domain.py` | AOI minus the sea (Water Body Mask), minus JRC permanent water, minus the reference's exclusion layers; intersected with an analysed extent when the reference has one. |
| 3. Align | `align.py` | Each product on its **own native grid**, encoding harmonised to 1/0/255 from the config, observations in the window unioned (cumulative) or taken within +/- days of a date (single_date). Sources: a STAC API, or local files. |
| 4. Evaluate | `evaluate.py` | The reference aggregated to each product pixel (flood share > 0.5); pixels the product has no data for are left out and reported as lost coverage. |
| 5. Table | `table.py` | One row per product, sorted by F1, metric rank and TOPSIS rank, CSV and Markdown, plus the one-line note. |

## Encoding and no-data

`1 = flood`, `0 = non-flood`, `255 = no-data` everywhere (plan rule 2). Each
product's own values are mapped from the config (`encoding`), never guessed.
A product's companion layers can turn pixels into no-data (`nodata_masks`):
**GFM's Exclusion Mask is no-data**, the product's own statement of where SAR
flood mapping does not work. It lowers coverage; it never becomes a non-flood
claim the product did not make. Verified on the GFM files 2026-10-11: flood
extent 1/0/255, exclusion mask 1 = excluded.

## Output

`<output_dir>/agreement_table.csv`, `agreement_table.md` and `agreement.json`
(the payload). The payload carries the table, the note, the confusion matrix
and coverage per product, the domain census, the reference's description, the
run's stated assumptions, and `ranks_compared` (with a caveat when fewer than
three products were evaluated). Field map: vault note
*Accuracy metrics payload fields*.

## Kelani 2025, Cyclone Ditwah (run 2026-10-11)

Reference: UNOSAT FL20251128LKA flood extent, one dissolved layer over 26 Nov -
2 Dec 2025, Sentinel-2, not field validated. Domain: the kelani-2025-west
rectangle minus 214.9 km2 of sea and 1.6 km2 of permanent water = 1,311.7 km2,
of which 61.0 km2 is reference flood (4.7%).

(This run predates 2026-11-22; since then the table also carries overall accuracy and the reference flood share -- see Every metric below.)

| Product | Coverage | Precision | Recall | F1 | IoU | MCC | Metric rank | TOPSIS rank |
|---|---|---|---|---|---|---|---|---|
| GFM (Sentinel-1) | 0.121 | 0.962 | 0.307 | 0.465 | 0.303 | 0.504 | 1 | 1 |
| OPERA DSWx-HLS | not evaluated: Earthdata login needed | | | | | | | 4 |
| OPERA DSWx-S1 | not evaluated: Earthdata login needed | | | | | | | 2 |
| MODIS MCDWD | not evaluated: no archive for Nov 2025 | | | | | | | 3 |
| VIIRS VCDWD | not evaluated: no archive for Nov 2025 | | | | | | | 5 |

GFM: 432,890 pixels compared (20 m) of 3,590,102 in the domain. Only 2 of 8
passes held data over the AOI; all of GFM's flood came from the 2 Dec pass.

## Decisions (user, 2026-10-11)

- Temporal mode **cumulative**: the reference has no per-polygon dates.
- Domain **AOI minus the cloud layer** (which does not touch this AOI). The
  reference ships no analysed extent, so unmapped ground counts as non-flood.
- **GFM only for now.** OPERA is behind an Earthdata login (PO.DAAC answered
  403); add the downloaded tiles as `source: files`. MODIS and VIIRS have no
  archive for the event.
- **AOI = the kelani-2025-west rectangle**, not the basin outline.

## Findings the interpretation must carry

- **The reference is Sentinel-2 (optical)**, though named "Multisensors". The
  validation plan assumed radar. An optical reference misses flood under cloud,
  so SAR flood there is scored as false alarm; optical products share that
  blind spot.
- **Selection effect.** Reference flood is 4.7% of the domain but 17.1% of
  GFM's compared pixels: the Exclusion Mask leaves mostly open floodplain.
- **Equi7 pixels are not 20 m on the ground** over Sri Lanka: GFM's grid is
  azimuthal equidistant centred on 94E 47N, and a nominal 400 m2 pixel covers
  about 365 m2 here. Counts and metrics are unaffected; areas use the ground
  size (`pixel_ground_m2`).
- **One product evaluated** -- no consistency claim against TOPSIS is possible.

## Known limits

- One event, one AOI, one reference.
- The threshold is strict ("exceeds 0.5"); on a product grid aligned with the
  10 m reference, exactly-half edge pixels count as non-flood.
- HDX data is CC BY-SA: credit UNOSAT.

## Gampola 2025 and the hardcoded reference (2026-11-18)

The MAIN thesis experiment is `gampola-2025`: AOI 80.5460-80.6310 E,
7.1368-7.2450 N, 26 Nov - 4 Dec 2025. References: Copernicus EMS EMSR851
AOI09 "Gampola" (GeoEye 0.5 m, 4 Dec, primary, `config.yaml`) and AOI03
"Mahaveli Ganga River" (Legion 1.6 m, 3 Dec, `config_aoi03.yaml`, generated
from the first by `make_aoi03_config.py`). Provenance and checksums:
`experiments/gampola-2025/validation/reference/README.md`.

**The reference is looked up, not passed** (user, 2026-11-18). With no
`config`, `accuracy_metrics` takes the session's AOI to `core/references.py`:
an AOI overlapping an experiment's box by at least half of the smaller box is
that experiment, and its primary reference is used; `reference` names
another (`emsr851-aoi03`). Any other AOI fails with "no reference flood map is
configured for this area" -- never a borrowed reference.

**Reading an EMS reference.** One `observedEventA` layer holds flooded area,
flood trace and a landslide, so the reader filters by attribute from the
`.dbf` (`flood_where`; flood trace counts as flood, user decision). The
landslide polygon leaves the domain (`exclude_layers` item with `where`), as
do the `notAnalysedA` polygons. The modelled layers (`modelledEventA`,
`floodDepthA`, FABDEM) are never a reference.

**The analysed extent is now applied.** `domain.py` said since the start
that `analysed_extent_layer` is applied; it was only recorded. Fixed
2026-11-18 -- without it, the parts of the (deliberately larger) Gampola AOI
the reference never looked at would count as "not flooded". The census
reports `outside_reference_analysed_km2`.

First run (2026-11-18, free: GFM via STAC, GIBS, JRC):

| Reference | Domain | Reference flood | GFM coverage | GFM F1 |
|---|---|---|---|---|
| EMSR851 AOI09 | 17.65 km2 (84.15 km2 of the AOI outside the analysed area) | 0.841 km2 | 8.6 % | 0 (no flood mapped where it saw) |
| EMSR851 AOI03 | 35.38 km2 | 0.738 km2 | 8.2 % | 0 |

MODIS and VIIRS 2-Day: 8 composites each, every pixel "insufficient data"
on every day over this AOI -- "8 observation(s) in the window, none with
valid pixels over the AOI". OPERA DSWx: not downloaded (Earthdata login).

## Kelani basin 2025 -- the main experiment (2026-11-18, later the same day)

`kelani-basin-2025`: AOI 79.9450-80.1550 E, 6.8800-7.0000 N, 26 Nov - 4 Dec
2025. Reference: Copernicus EMS EMSR851 AOI02 "Kelani Ganga River"
(PlanetScope 3.9 m, 30 Nov 2025; semi-automatic extraction; all "Flooded
area"). Config generated by `experiments/kelani-basin-2025/validation/make_config.py`;
OPERA files are the ones downloaded for kelani-lower-2025 (same MGRS tiles),
inside the window only (3 DSWx-HLS, 14 DSWx-S1; none after 2 Dec).

**Hardened for the console** (the user runs it from the front end):
- a `config` path that does not exist, or one belonging to ANOTHER
  experiment than the session's AOI, is replaced by the AOI's own reference
  and the payload says so (`reference_choice.note`);
- an `aoi_bbox` equal to the experiment's AOI is not a "small window";
- `event_id` is accepted and ignored (Argus copies it into every task; the
  tool used to fail as `signature_mismatch`);
- with `topsis.path: null`, the session's own stored ranking fills the TOPSIS
  column (`topsis_source`);
- AOIs match experiments by intersection over union (>= 0.5): the basin box
  contains the lower-Kelani one, and a share of the smaller box tied them.

First run through Monty's task runner, as a console turn (2026-11-18; the
TOPSIS ranks in that check came from another session's ranking, only to show
the column fills): domain 153.12 km2, reference flood 30.12 km2. F1 order:
DSWx-HLS 0.576 (coverage 0.70), GFM 0.497 (coverage 0.14, precision 0.94),
DSWx-S1 0.292, VIIRS 0.214, MODIS 0.086 -- every product evaluated.

## Every metric (2026-11-22)

**Every metric on every reference-map comparison** (user guardrail 2026-11-22). The agreement table carries coverage, overall accuracy, precision, recall, F1, IoU, MCC and the reference flood share for every product, with metric rank and TOPSIS rank; `table.METRICS` is the one list, `check_complete` refuses a table in which an evaluated product lacks one, and `core/agreement_table.py` writes the full table under every answer that used an accuracy result -- in Python, after every model has finished, so no metric can be left out. Values are copied from the result, never recomputed; undefined is n/a.
