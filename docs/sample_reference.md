# Design: a stratified labelled sample as a second reference

Status: **design, pilot in progress**. Nothing here is a thesis result yet.
The process figure is in the README ("Planned: validation with a labelled
sample"). `accuracy_metrics` ([accuracy_metrics.md](accuracy_metrics.md))
compares each product with an operational reference map; this compares each
product, and the reference map itself, with an analyst-labelled sample.

## Why a second reference

UNOSAT FL20251128LKA, the Ditwah reference, is thinner than its name:

- It is one dissolved polygon for the whole country, built from
  `S2_Till02Dec_Flood_Merge2.shp` (its lineage): Sentinel-2 only, despite the
  "Multisensors" name. No per-polygon date; "To be evaluated"; "Not yet field
  validated".
- Its cloud and analysed-extent layers cover only the central highlands
  (80.256-81.09 E, 6.88-7.75 N). Everywhere else, cloud, "not imaged" and "dry"
  all count as non-flood. The Kelani run excluded 0 cloud pixels for this
  reason (`agreement.json`, `excluded_layers`).
- Over Mannar it rests on essentially one image, Sentinel-2 30 Nov 2025 05:16
  UTC, with 33-57% tile cloud on 44PLQ / 44PMQ; 25 Nov was 93-100% and 27 Nov
  100%.

So the reference is a lower bound on flood. A product that sees water under
UNOSAT's cloud is scored as a false alarm. The sample measures that.

Step-by-step walkthrough with a flow diagram (strata, labels, estimators,
final metrics): vault note `Head/Uncertainty/Knowledge/Labelled-sample
validation - walkthrough`.

## What the sample estimates

For one AOI and one event window, with confidence intervals:

- each evaluated product's precision, recall, F1 and IoU;
- the flood area;
- the completeness of the reference map (its recall against the sample).

## Decisions (user, 2026-10-19)

| Decision | Value |
|---|---|
| AOI | **Mannar**, `[79.88, 8.70, 80.22, 9.10]`, slug `mannar-2025` |
| Window | 2025-11-21 to 2025-12-02, Mannar local time |
| Peak day | **2025-11-28** (IFS rain about 142-183 mm that day) |
| Sample size | **450 points** per AOI (reasoning below) |
| S3 buffer | **250 m**, provisional -- see TODO |
| OPERA | fetched with the user's Earthdata login, before the frame is frozen |
| OPERA flood classes | DSWx-HLS **1, 2** (open + partial surface water); DSWx-S1 **1, 3** (open water + inundated vegetation) -- see below |
| PlanetScope | **not used** |
| Chips | rendered on the server (Option 1), shown in a local labelling page |

**TODO(sample-buffer): justify the 250 m S3 buffer from the literature.** It is
a placeholder. It sets how much likely-wet ground leaves S4, and S4 drives the
flood-area interval. Needed: published evidence on where flood-map omission
errors concentrate relative to mapped flood edges (distance from the edge,
pixel-size multiples for 20-30 m products). Until a source is recorded in
`docs/SOURCES.md`, every output states the buffer as an assumption.

## OPERA flood classes (user decision, 2026-10-19)

OPERA DSWx maps *water*, not flood. Inside the domain JRC permanent water is
already removed, so water there is read as flood. Which water classes count:

| Product | Flood | Not flood | No-data (lost coverage) |
|---|---|---|---|
| DSWx-HLS | 1 open water, 2 partial surface water | 0 | 252 snow/ice, 253 cloud / shadow / adjacent, 254 ocean, 255 fill |
| DSWx-S1 | 1 open water, 3 inundated vegetation | 0 | 250 HAND masked, 251 layover/shadow, 255 fill |

- The flood classes are **OPERA's own binary-water (BWTR) union** in both
  products, so the choice follows the producer's definition of water.
- The no-data classes are the ones both specifications call invalid for a
  water/no-water decision. Like GFM's exclusion mask, they lower coverage and
  are never a non-flood claim.
- **Partial surface water is at least 20% open water**, per the
  specification. A tutorial that quoted 50% is wrong.
- Consequence to state with every OPERA figure: counting partial water and
  inundated vegetation makes OPERA's flood more inclusive than GFM's, which
  maps open water only. Any difference in recall or precision between OPERA
  and GFM partly reflects this definition, not only skill.

Sources: the WTR layer in Table 4-1 of each specification, JPL D-107395 Rev B
(DSWx-HLS v1.0.1, 2024-07-10, p. 10) and JPL D-108761 (DSWx-S1, 2024-08-16,
p. 9). Audit entries: `docs/SOURCES.md` section 8d.

## Strata

Built from one stratification map, frozen before any labelling.

| Stratum | Definition | Purpose |
|---|---|---|
| S1 product flood | any evaluated product says flood | precision |
| S2 reference-only flood | reference says flood, no product does | omission; reference false alarms |
| S3 likely wet | dry in every map, and JRC occurrence > 0 or within 250 m of S1/S2 | where omission concentrates |
| S4 far dry | everything else in the domain | rare omission, heavy weight |

S2-S4 are each split by "observed by at least one evaluated product", with
about two thirds of their points in the observed part, so a product with low
coverage still gets enough usable points. GFM covered 12% of the Kelani domain.

The domain is the one `accuracy_metrics` uses: AOI minus sea minus JRC
permanent water, on the evaluation grid (EPSG:32644, 10 m). The unit is one
10 m cell, labelled by its majority class. A product's claim at a point is its
own native pixel there.

## Sample size: why 450

Allocation 100 / 100 / 100 / 150 (S1 / S2 / S3 / S4). Kelani areas, with
**assumed** true-flood shares S1 0.90, S2 0.80, S3 0.15, S4 0.005, and S3
assumed at 100 km2:

| Option | n | Flood area 95% CI | GFM precision | GFM recall | Effort at 2 min/point |
|---|---|---|---|---|---|
| lean | 300 | +/- 20.6 km2 | +/- 0.068 | +/- 0.033 | ~10 h |
| **chosen** | **450** | **+/- 15.3 km2** | **+/- 0.059** | **+/- 0.025** | **~15 h** |
| strong | 600 | +/- 12.3 km2 | +/- 0.059 | +/- 0.020 | ~20 h |

Why 450 and not more:

- Precision is set by S1 alone. At 100 points it is already about +/- 0.06;
  more S1 points buy little.
- The flood-area and recall intervals are driven by S4: about 1,150 km2 at a
  0.5% flood share is about 6 km2 of hidden flood, weighted by the whole
  stratum. Going from 450 to 600 narrows the area interval by only 3 km2 for
  5 more hours. A sharper S3 moves likely-wet ground out of S4 more cheaply.
- Each stratum keeps at least 100 points, which is within the range Olofsson
  et al. (2014) recommend for rare classes.
- 10% of points (about 45) are labelled twice, for inter-labeller agreement.

The shares are assumptions until the pilot replaces them with measured ones.
If the pilot shows a much higher unobservable share (cloud, canopy), the
effective n falls and the allocation is revisited before the main sample.

## How the two papers are used

- **Olofsson et al. (2014)**, good practice for estimating area and assessing
  accuracy of land change. Used for the design rules: a probability sample, so
  every point has a known chance of selection; stratification, so rare classes
  are sampled enough; the sample-size reasoning and a minimum per stratum;
  area-weighted estimates instead of pixel counts; and reporting every
  estimate with a confidence interval.
- **Stehman (2014)**, estimating area and map accuracy for stratified random
  sampling when the strata differ from the map classes. Used for the
  estimators. Olofsson's formulas assume the strata ARE one map's classes.
  Here one stratification serves several products and the reference, so each
  product's classes cut across the strata. Stehman gives the unbiased ratio
  estimators for precision, recall and area in that case. It is also what
  lets a product added after the freeze be evaluated on the same sample.

## Label imagery and independence

A label counts for a product only if its image does not come from a sensor
the product uses.

| Source | GFM | DSWx-S1 | DSWx-HLS | MCDWD / VCDWD |
|---|---|---|---|---|
| Sentinel-2 L2A (Earth Search) | yes | yes | no | yes |
| Landsat 8/9 C2 L2 (Planetary Computer) | yes | yes | no | yes |
| VHR orthos (CEMS EMSR851) | yes | yes | yes | yes |
| Sentinel-1 | not used for labels |  |  |  |

Sentinel-2 is also the reference's own source; labels from it will tend to
agree with UNOSAT, and the text says so.

Mannar, in the window: Landsat 9 27 Nov (38% scene cloud, Tier 2 -- weaker
geolocation, record the tier and use only on uniform ground); Sentinel-2
30 Nov (33-57% tile cloud). No VHR image in the window (EMSR851 AOI18 is from
22 Dec). So **DSWx-HLS cannot be labelled independently at Mannar**; its row
reports that.

A pre-event image is shown for every point (the `fetch_lulc_data` scene).
November is Maha paddy season under the Giant's Tank scheme: standing paddy
water is `preexisting_water`, not flood.

## Labelling protocol

- Product layers and strata are hidden; points come in random order.
- Classes: `flood`, `not_flood`, `preexisting_water`, `unobservable` (reason:
  `cloud`, `cloud_shadow`, `canopy`, `built_up`, `no_image`).
- Confidence 1-3.
- **Cumulative rule (in code):** `flood` if any in-window observation shows
  flood. `not_flood` only if a clear observation on or after the peak day
  shows dry and none shows flood. Anything else is `unresolved`: excluded and
  counted, never guessed.

## Record format

In `experiments/<slug>/validation/sample/`:

- `manifest.yaml` (written at freeze): experiment, `frozen_at`, seed, domain
  definition, input hashes (sha256), strata definitions and areas,
  allocation, window, `tolerance_days`, `peak_date`, label key version,
  permitted sources per product.
- `sample_points.csv` (written by code): `point_id`, `stratum`, `x`, `y`
  (EPSG:32644), `lon`, `lat`, `stratum_area_km2`, `n_h`, `weight_km2`,
  `draw_order`.
- `labels.csv` (one row per point and image): `point_id`, `obs_id`,
  `labeller`, `round` (`primary` | `qa`), `labelled_at_utc`, `source`,
  `image_id`, `image_datetime_utc`, `class`, `unobservable_reason`,
  `confidence`, `pre_event_image_id`, `blind`, `elapsed_s` (seconds on the
  point, for the pilot's timing), `notes` (no personal data).
- `product_values.csv` (written by code, hidden from labelling): the
  reference's and each product's class at every point.
- `strata.tif`, `images.json` (the in-window images, and those excluded at
  tile cloud 100%), `chips/` (the rendered PNGs, a cache).

The per-point label is computed from `labels.csv`, never typed.

## Where it lives

| Piece | File |
|---|---|
| Strata, allocation, draw, freeze | `core/workflows/validation/sample.py` |
| Image search and chips | `core/workflows/validation/chips.py` |
| Labelling page | `api/label_server.py`, `api/label_page.html` (127.0.0.1:8790) |
| OPERA download (user-run) | `scripts/fetch_opera_dswx.py` |
| Tests | `tests/test_sample_frame.py` |

    python -m core.workflows.validation.sample <config.yaml> pilot
    python -m api.label_server <config.yaml> pilot

## Finding: Mannar cannot be labelled inside the window (2026-10-20)

Read from each image's own cloud flags (Sentinel-2 SCL, Landsat QA_PIXEL):

- **2 of the 50 pilot points had any clear view between 21 Nov and 2 Dec
  (4%).** On a 0.01-degree land grid the same holds: 5.0% of Mannar's land was
  ever clear, all of it on Sentinel-2 30 Nov; every other image was 0%.
- After the window it opens: 42 of 50 points clear at least once between 3 and
  15 Dec (Landsat 9 4 Dec Tier 1; Sentinel-2 5 and 15 Dec) -- but 9-12 Dec
  brought a second rain wave, and those views measure the recession, not the
  event.
- Consequence: an in-event optical reference sample is infeasible at Mannar,
  and UNOSAT's Mannar layer rests on the few gaps in that cloud. This is
  reported as a finding, not filled. The frozen Mannar pilot stays on disk as
  its evidence; no Mannar labels are taken.

Same check, same window, same grid (clear land on Sentinel-2 30 Nov):
Kelani 94.1%, Puttalam south 78.9%, **Puttalam north 71.2%**, Mannar 5.0%.
The indirect-damage AOI moved to Puttalam north (user decision 2026-10-20).

**Caveat for every in-window label at these AOIs:** the only clear image is
Sentinel-2 30 Nov, which is UNOSAT's source and a DSWx-HLS input. Those labels
are independent of GFM and DSWx-S1 only.

## MODIS and VIIRS recovered from GIBS (2026-10-20)

The LANCE NRT archive keeps about a week, so MCDWD and VCDWD were "not
evaluated: no archive" in the Kelani table. GIBS keeps the rendered layers;
`scripts/fetch_gibs_flood.py` recovers the class values exactly from them (see
`docs/SOURCES.md` 8d). Fetched for Kelani, Mannar and Puttalam, 21 Nov - 3 Dec,
all six layers (MODIS / VIIRS x 1-, 2-, 3-Day), into
`experiments/<slug>/validation/gibs/`.

- 24/25-29 Nov: 100% insufficient data on every layer at all three AOIs.
- First views 30 Nov - 2 Dec. **MODIS and VIIRS see Mannar where Sentinel-2
  could not** (e.g. VIIRS 1-Day 2 Dec: 85% observed, 10.1% flood), because two
  to four daily passes find gaps a single morning pass misses.
- Kelani shows at most about 1% flood at 250 m: a narrow urban river corridor.
- Still open: which composite stands for each product, and wiring the files
  into the validation configs (`source: files`, flood = [3], not flood = [0, 1],
  no-data = [255, 252, 253, 254]).

## Final AOI pilot: Sentinel-2 only (user decision, 2026-10-21)

The kelani-lower-2025 pilot is labelled from Sentinel-2 alone (25 Nov, 95%
cloud; 30 Nov, 3% cloud). The EMSR851 VHR orthos are **not** added for now
(licence for derived labels unchecked). Consequence, stated with every result:
the 30 Nov scene is UNOSAT's source and a DSWx-HLS input, so the pilot labels
are independent of GFM and DSWx-S1 only.

**Window changed to 24 Nov - 1 Dec (user decision, 2026-10-21); Landsat
asked for.** The window decides which product passes make the strata, so the
pilot frame was rebuilt with the same seed (no labels had been made); the
21 Nov - 2 Dec frame is kept as `sample/pilot_superseded_window_2025-11-21_12-02/`.
All 2 Dec passes left the frame. Landsat is searched for every pilot already;
in this window there is one pass, LC09 27 Nov, 100% cloud over the AOI
(QA_PIXEL), so the label images are unchanged. LC08 19 Nov (27.8% clear) and
5 Dec (83.1% clear) lie outside the window and are not used: a label must
come from inside the window (confirmed by the user 2026-10-21: "Keep labels
inside 24 Nov - 1 Dec only. No Landsat outside the window."). Planetary Computer is the Landsat source here,
which `docs/SOURCES.md` section 9 lists as dropped -- the user's ruling on
that is still open.

**MODIS / VIIRS: the 2-Day composite (user decision, 2026-10-21)** -- "so that
we have temporal integrity". A 2-Day file dated D combines D-1 and D, so only
files whose both days are inside the window are in the config (25 Nov - 1 Dec;
the 24 Nov file reaches 23 Nov and is left out). In the window MODIS sees the
AOI only on the 1 Dec file, VIIRS on 30 Nov and 1 Dec. Flood in the domain:
MODIS 0.96 km2, VIIRS 2.28 km2 (250 m pixels).

### Pilot result, kelani-lower-2025 (labelled 2026-10-21)

`python -m core.workflows.validation.pilot <config>` writes `report.md` and
`report.json` beside the pilot (estimators: `core/workflows/validation/estimate.py`,
Stehman 2014; tests: `tests/test_estimate.py`). Planning numbers only:

- **17 s per point** (median; 90th percentile 36 s) -- 450 points is about
  2.5 h, not the 15 h assumed above.
- 25 Nov: cloud at all 50 points. Every label rests on 30 Nov, two days after
  the peak: a cell dry on 30 Nov may have been wet on 28 Nov, so the labelled
  flood is a lower bound at the peak and recall is scored against it.
- 49 of 50 points resolved. Flood share: S1 0.82, S2 0.91, **S3 0.27**, S4 0.
- Flood area 49 +/- 26 km2 (UNOSAT maps 20.5 km2); UNOSAT precision 0.89,
  recall 0.43 (not independent -- same Sentinel-2 source).
- **GFM maps no flood in the window** (0.00 km2; it observed 18.9 km2 of the
  domain). Its precision is undefined at any sample size; recall and coverage
  only. DSWx-S1 3.03 km2 of flood (its only pass, 26 Nov, is before the peak).
- DSWx-HLS cannot be scored: every label is Sentinel-2.
- S1 is 18.68 km2, of which 13.09 km2 is flood in DSWx-HLS alone -- the one
  product the sample cannot score. Only 5.59 km2 is flood in a scorable
  product, so even 600 points under the current design give DSWx-S1, MODIS and
  VIIRS about 10 map-flood points each (precision +/- 0.15-0.25).
- Ten label rows carry confidence 1 or a note ("border" x4, "small portion
  flooded", "flood on structure"): the 10 m majority rule needs an example
  page for mixed cells before the main sample.

### The 250 m buffer: what it does (assumption, kept)

It is used in one place, `sample.stratify`: a cell dry in every map goes to
S3 if JRC ever saw water there **or it lies within 250 m of S1/S2**, else to
S4. It changes no estimate's expected value -- every stratification is
unbiased under a design-based estimator -- only where the points go, and so
how wide the intervals are. Here it puts 90.35 km2 in S3 and leaves 14.0 km2
in S4. The pilot found 27% flood in S3 and none in S4, so the buffer does
separate likely-wet from dry; S3 is the flood-area interval's main driver.

## Puttalam north (user decision, 2026-10-20)

| Item | Value |
|---|---|
| AOI | `[79.78, 7.88, 80.08, 8.33]`, slug `puttalam-2025` -- Puttalam town, Mi Oya mouth, Kala Oya and Eluwankulama, Wanathawilluwa |
| Window | 2025-11-21 to 2025-12-02, local; peak day 2025-11-28 (IFS about 224 mm at Puttalam town that day, about 430 mm over 27-29 Nov) |
| Reference | UNOSAT FL20251128LKA, about 191 km2 of flood in the box (screening) |
| Design | as Mannar: strata S1-S4, 50-point pilot, 450-point main |

## The pilot

50 points on Mannar, drawn with their own seed, allocated in proportion to the
main sample (11 / 11 / 11 / 17). **Pilot points are not reused in the main
sample**: the label key may change after the pilot. The pilot measures:

- time per point (from `labelled_at_utc`);
- the unobservable and unresolved shares, per stratum and per source;
- where the label key is unclear.

Its frame uses the products available at the time; the main frame is frozen
only after OPERA is in.

**OPERA over Mannar** (downloaded 2026-10-19, 6 DSWx-HLS + 26 DSWx-S1 water
layers; window union on the native 30 m grid, over the AOI box before the
domain removes sea and permanent water):

| Product | Observations with data | Flood | Not flood | No-data |
|---|---|---|---|---|
| DSWx-HLS | 2 of 6 | 34.2 km2 | 14.2 km2 | 1,615.7 km2 |
| DSWx-S1 | 25 of 26 | 577.3 km2 | 1,086.8 km2 | 0.0 km2 |

- **DSWx-HLS is nearly blind here**: about 3% of the box was ever clear. It
  also cannot be labelled independently at Mannar (its inputs are Sentinel-2
  and Landsat, the only label images in the window). In the main frame it will
  fail the 50-usable-points check; its row will say why rather than report a
  number from a handful of points.
- **DSWx-S1's flood over the box includes the sea**, which it does not mask
  (it has no ocean class). The domain removes the sea before any statistic,
  so this does not reach a metric.
- No DSWx pass falls on the peak day (28 Nov): S1 on 26 Nov (before), 1 and
  2 Dec (after); HLS clear only on 25 and 30 Nov.

**Frozen 2026-10-19** (`experiments/mannar-2025/validation/sample/pilot/`),
GFM only -- OPERA not yet downloaded, MODIS and VIIRS have no archive:

| Stratum | Area km2 | Points |
|---|---|---|
| S1 | 128.8 | 11 |
| S2 obs / unobs | 113.0 / 24.2 | 7 / 4 |
| S3 obs / unobs | 207.5 / 135.5 | 7 / 4 |
| S4 obs / unobs | 49.4 / 599.0 | 11 / 6 |

Domain 1,257.3 km2 (AOI 1,653.5 km2 on the evaluation grid, less 390.2 km2
sea and 6.0 km2 permanent water); UNOSAT flood 247.3 km2 inside it. 228 point-images: the
31 Oct pre-event mosaic, Sentinel-2 25, 27, 30 Nov, Landsat 8 26 Nov and
Landsat 9 27 Nov (both Tier 2); three tiles were excluded at 100% cloud.

## References

- Olofsson, P., Foody, G. M., Herold, M., Stehman, S. V., Woodcock, C. E., &
  Wulder, M. A. (2014). Good practices for estimating area and assessing
  accuracy of land change. *Remote Sensing of Environment*, 148, 42-57.
- Stehman, S. V. (2014). Estimating area and map accuracy for stratified
  random sampling when the strata are different from the map classes.
  *International Journal of Remote Sensing*, 35(13), 4923-4939.

Audit entries: `docs/SOURCES.md` section 8e, with the image services. **Open:**
Landsat chips come from Microsoft Planetary Computer, which section 9 of
`SOURCES.md` drops by decision -- pending the user's ruling.
