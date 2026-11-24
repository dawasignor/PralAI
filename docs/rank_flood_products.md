# `rank_flood_products`

Ranks the five NRT flood-mapping products for one event by TOPSIS, and returns
the decision matrix, the evidence behind every score, the ideal points and the
ranking. Feeds thesis outputs 3 and 4.

**It fetches nothing.** Every other workflow in `core/workflows/` reaches a
catalogue or a bucket; this one consumes what they produced. It is the last
call in a chain, not a starting point.

Specification: `flood_dataset_MCDM_template_v3.xlsx` (2026-09-22). This module
implements steps 1-7 of its Spec sheet. Steps 8 and 9 -- a weighted-sum
robustness check and a Spearman comparison of the two rankings -- **are not
implemented**, so no output of this workflow may be described as
method-agnostic.

## Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `cloud_pct` | float | yes | | AOI cloud cover at the optical passes, 0-100 (since 2026-11-22; see Cloud and wind at the passes). |
| `wind_ms` | float | yes | | AOI wind speed, m/s, >= 0. |
| `veg_pct` | float | yes | | Share in the dense-vegetation class, 0-100. |
| `steep_pct` | float | yes | | Share classified steep or very steep (slope >= 15°, Horn's method), 0-100. |
| `urban_pct` | float | yes | | Share above the BUI threshold, 0-100. |
| `mode` | str | no | `WEIGHT` | `WEIGHT` scales criterion weights; `MATRIX` multiplies matrix cells. See below. |
| `base_weights` | dict/list | no | equal (1/12) | Per-criterion starting weights (see Weights). |
| `weighting` | string | no | `event_first` | `event_first` (user decision 2026-11-22) or `multiplicative` (the workbook's rule). |
| `parameters` | dict | no | | Overrides for the author rules. |

**A missing index is reported, never imputed.** A guessed index changes the
ranking invisibly, which is the one failure this workflow cannot let a reader
detect.

## What the event changes

**The weights, not the scores.** A product is no better at seeing through cloud
on a clear day; what changes is how much its cloud-blindness matters. So each
index scales the weight of the criterion it governs, and the product scores are
constant across events.

`MATRIX` mode is the specification's literal alternative -- the index
multiplies the matrix cells instead -- and it is implemented because the
specification describes it, not because it works. **Under vector normalisation
a uniform column multiplier cancels.** The workbook's own TC5 and TC6 show
dense vegetation at 40% and 70% giving identical rankings. It is kept so the
choice is visible and refutable rather than quietly dropped.

## Why high cloud cover no longer rejects a product

**Changed 2026-09-22, by the user, for the thesis.** `cloud_upper` and
`cloud_gate` both moved from 80 to 100. They are different things: the first is
where the penalty curve saturates, the second is where an optical product
stopped being a candidate at all. At 100 the gate cannot fire, because cloud
cover has nowhere above to go.

The mechanism is intact rather than deleted -- `cloud_gate` is still a
parameter and still reported, and setting it below 100 restores the exclusion.

Three reasons, and the first is the one that survives scrutiny:

**1. The gate fired on the wrong statistic.** `cloud_pct` is a mean over the
whole event window -- 192 hours for the Kelani event, including every night.
Feasibility for an optical product is not a property of that mean; it is a
property of each daytime overpass. Measured on the Kelani event, whose window
mean is 91.67%:

| | Overpasses | Below 80% cloud |
|---|---|---|
| ~10:30 local (MODIS Terra, HLS) | 8 | **1** (19%) |
| ~13:30 local (MODIS Aqua, VIIRS) | 8 | **1** (45%) |

So the optical products had **2 usable opportunities in 16**, both on
2025-11-30 after the rain stopped. Rejection asserts zero; the record shows a
small non-zero number. The gate was making a stronger claim than the data
supports.

**2. A two-row matrix is a degenerate TOPSIS.** With three of five alternatives
gone, one survivor is the ideal on every criterion it wins and the anti-ideal
on every one it loses, so the closeness values are near 1 and near 0 **by
construction** -- the Kelani run gave 0.827 and 0.173. That is not a ranking, it
is a pair rendered as numbers, and it cannot demonstrate the method.

**3. Outputs 5 and 6 need a continuous response.** Sensitivity analysis and
SMAA perturb the inputs and watch the ranking move. A gate is a discontinuity:
perturb cloud across 80% and the alternative set changes, so rank-acceptability
is undefined across the boundary rather than merely different.

### What this costs, and it is not nothing

**It moves cloud from a precondition into the compensatory pool**, and the
vault's own [[Scoring criteria]] ruling 2 warns about exactly that: "a
compensatory aggregation averages a top accuracy score against a zero
availability score and can still rank the product first". TOPSIS is
compensatory. On the Kelani event **MODIS MCDWD ranks 3rd** on the strength of
a 23-year archive and daily revisit, over an event it could barely observe.

The reconciliation is that ruling 2 governs **existence** -- does this product
cover this AOI and this date at all -- which is binary and permanent. Cloud is
**conditional observability**, varies hour by hour, and was already represented
as a scored criterion (`W1`). The two are different kinds of thing. But the
compensatory hazard is real here regardless, so:

> **A rank is no longer a usability claim.** Check `W1` and the cloud index
> before reading one as a recommendation. Output 3 states this in the gate
> section and carries the dated change note beside the numbers.

The penalty curve is also gentler everywhere, not only past 80:

| Cloud | g, upper = 80 | g, upper = 100 |
|---|---|---|
| 50% | 0.182 | 0.109 |
| 70% | 0.586 | 0.289 |
| 80% | 1.000 | 0.445 |
| 91.67% | 1.000 | 0.718 |

## Criteria

Twelve: six product criteria (P1-P6), three weather (W1-W3), three terrain
(L1-L3, land characteristics; T1-T3 before 2026-11-19). Every weather and terrain criterion is a cost, and so are spatial resolution and
revisit. The register, with each criterion's direction, scale and scoring rule,
is in `core/workflows/_mcdm_data.py` and reproduced in output 3.

**The evidence register is the point.** Thirty cells -- six susceptibility
criteria times five alternatives -- each carrying `basis`, `contested`,
`rationale` and source keys:

- **D** product documentation -- the product's own spec or user guide
- **S** sensor physics -- stated for one product, applied by sensor type
- **R** author rule -- a modelling choice, with no document behind it

Five cells are marked contested: a second coder could reasonably score them
differently. They are carried into the output with the weight their criterion
holds, because a sensitivity analysis that does not know which cells are soft
is testing the wrong thing.

`_mcdm_data` checks at import that the evidence register and the matrix agree
cell for cell. They hold the same thirty numbers, and two copies of a number is
one copy and one bug waiting.

## Unresolved

- **P2 (revisit).** The author's instruction on SAR revisit was cut off. GFM
  and DSWx-S1 keep the documented 6-12 day range with the worst case (12) used.
  This favours the optical products on P2 and travels into the output as
  `p2_pending`.
- **Weights: event criteria first** (2026-11-22). **Event criteria are weighted first** (user decision 2026-11-22). Each of the six event criteria starts at the base weight 1/12 (0.0833) and is raised by this event's index: W1 by the cloud curve g(cloud) (0 up to 20% cloud, 1 at 100%), W2 by the wind ramp f(wind) (0 up to 5 m/s, 1 at 15 m/s), L1 by the dense-vegetation share, L2 by the steep share, L3 by the built-up share -- so 52% dense vegetation makes L1 1/12 x 1.52 = 0.127. W3 (daylight) is not raised: darkness comes every night. The weight left after the event criteria is divided equally among the six product criteria P1-P6. Over the Kelani basin with the window-mean cloud and wind (cloud 77%, wind 3.4 m/s, vegetation 52.4%, steep 5.39%, built-up 0.04%): W1 0.116, W2 0.083, W3 0.083, L1 0.127, L2 0.088, L3 0.083, P1-P6 0.070 each; with cloud and wind read at the passes (also 2026-11-22: cloud 62.8%, wind 3.24 m/s) W1 is 0.101 and P1-P6 0.072 each. Before this the event could only LOWER a weight (base x index, renormalised), so no event criterion could ever outweigh a product criterion; that rule is kept as `weighting="multiplicative"` for the specification workbook's test cases.
- **Base weights are equal (1/12).** AHP and entropy weighting are not decided.
  An equal-weight ranking says what the criteria imply when nobody has said
  which matters more.
- **No WSM, no Spearman.** The specification's robustness check is absent.

## Verification

The port was checked against the workbook's own TestCases sheet: **all six
cases reproduce to 4.14e-13**, including TC5 ≡ TC6, which is the MATRIX-mode
cancellation.

**TC3 encodes the old 80% gate and no longer applies at the defaults.** It
still reproduces exactly when the old parameters are passed explicitly:

```python
rank_flood_products(cloud_pct=85, wind_ms=8, veg_pct=40, steep_pct=20,
                    urban_pct=15, parameters={"cloud_upper": 80,
                                              "cloud_gate": 80})
```

That is what shows the 2026-09-22 change to be a parameter change and not a
change of logic.

## The index mapping

`indices_from_payloads(weather, lulc, terrain)` fills the workbook's "TO MAP"
column. It returns the five indices plus, for each, where it was read from, how
it was reduced, and **what it is a share of**.

**Two of the five are shares of observed land, not of the AOI**, and the
specification asks for AOI shares. Cloud removes a quarter of the Kelani basin
from the Sentinel-2 scene and it does not remove a random quarter: cloud
concentrates over high ground where canopy is denser, and cities sit low. So
`veg_pct` is probably an underestimate and `urban_pct` an overestimate.

They are **not** scaled up to the AOI, because that assumes the unobserved part
resembles the observed part -- an assumption the cloud distribution is known to
violate, in a direction we can name. The observed share is used and labelled.

`urban_pct` is counted exactly when the BUI threshold falls on a histogram bin
edge, which 0.2 does; otherwise it is interpolated inside the straddling bin
and the interpolation is declared.

## Outputs

`core/mcdm_report.py` renders two documents, **deterministically -- no agent
writing**. A brief is judgement; a decision matrix is arithmetic with a
specification behind it, and asking a model to restate either would put a
paraphrase where a citation belongs.

- **Output 3**, the matrix *before* the ranking: criterion values, the evidence
  behind each, the weights, the parameters and the sources.
- **Output 4**, the ranking: normalised matrix, weighted matrix, ideal and
  anti-ideal points, separation measures and the order.

The split is the point. A reader who disagrees with the outcome has to argue
with output 3, where the evidence is, rather than with the arithmetic in
output 4.

## Cloud and wind at the passes (2026-11-22)

**Cloud and wind are read at the acquisitions, not over the window** (user decision 2026-11-22). Cloud (W1) is the mean of the AOI model cloud at the OPTICAL passes -- OPERA DSWx-HLS, MODIS MCDWD, VIIRS VCDWD -- because only optical sensors are hurt by cloud; wind (W2) is the mean of the AOI 10 m wind at the RADAR passes -- GFM, OPERA DSWx-S1 -- because only radar is hurt by wind. Pass times come from Module 1 (data_availability): exact for GFM, DSWx-S1 and DSWx-HLS; for MODIS and VIIRS, whose daily composites carry no time, the nominal overpass (Terra 10:30, Aqua 13:30, VIIRS 13:30 local SOLAR time, which is 10:39 and 13:39 civil time in Colombo) on every day of the window. The values come from Module 2's hourly series, interpolated between the two hours either side of each pass. Passes within 5 minutes are one acquisition (one Sentinel-1 pass gives both GFM and DSWx-S1). A pass on a missing hour is withheld and counted; with none left the index is not known -- the window mean is never used instead. Kelani basin: cloud 62.8% over 20 optical passes (window mean was 77.0%), wind 3.24 m/s over 5 radar passes (window mean 3.40), giving W1 0.101 and P1-P6 0.072 each.

**Guardrail: everything is converted to LOCAL time before scoring** (user rule 2026-11-22). Pass times (UTC), model hours (UTC) and nominal overpasses (local solar time) are all turned into the event's IANA zone (Module 1's `period.timezone`) before they are matched or counted, and a pass belongs to the window by its local date. A missing zone, an unknown zone, hours not stored in UTC or a stamp without a zone stop the run (`LocalTimeError`) -- nothing is guessed. Every time the code returns is local, with its offset.

The gate history below (the 91.67% window mean) is kept as the record of why the gate was lifted; it is no longer how the index is computed.

## Module 6 as a figure; user scores (2026-11-22)

User feedback on the Module 6 answer: the white score table did not say which
criteria are costs and which benefits, the weight story was hard to follow,
the Sources section listed bare keys, and the user wants to change weights AND
scores at this stage.

- **The figure** (`charts.decision_matrix_figure`, drawn by
  `mcdm_decision_matrix` into `output_dir`): benefit criteria (P3-P6, higher
  is better) and cost criteria (P1, P2, W1-W3, L1-L3, lower is better) in two
  bands; each cell coloured by its place between the row's best (blue) and
  worst (red), grey when all are equal; each weight a bar beside the equal
  start (1/12) with what the event did to it ("dense vegetation 52.4%: x
  0.52"); a weight below 0.001 is drawn faded. Module 6 offers no table any
  more (`tables.BUILDERS`); the Module 6 document places the figure
  (`mcdm_matrix_figure`).
- **The answer**: `result_view` gives Argus `reading_guide` -- four or five
  lines written from the payload -- the user's changes and the shape of a
  short answer, not the condensed payload.
- **Scores**: `scores` on both tools, `{"P2": {"gfm": 6}}` or
  `{"P2|gfm": 6}`, product by id or name; checked against the criterion's
  scale (binary 0/1, ordinal 0-2, ratio >= 0), never clipped. `scores_changed`
  lists each cell with the value it replaced; the evidence register keeps the
  documented score.
- **Module 6 -> Module 7**: the changes made at Module 6 are kept in its
  stored result (`user_choices`); `rank_flood_products` called with neither
  `weights` nor `scores` ranks with them (`weights_source` "user-stated at
  Module 6"). `use_module6=False` for library callers; the sensitivity sweep
  passes it, so Module 8 still sweeps the COMPUTED weights (open: it does not
  yet sweep around the user's).
- **Sources**: the matrix and the ranking list the stored weather, land-cover
  and terrain results the event values came from, with their dates, and the
  evidence documents S1-S11 in full (`attribution.MCDM`).

## Module 6 and Module 7 (2026-11-19)

User decision: the scores and weights are a step of their own, and the
ranking follows on the next turn.

- **`mcdm_decision_matrix` (Module 6)** takes the same five indices and
  returns the matrix, the evidence and every criterion's base, modulator and
  normalised weight -- built by the same code path as the ranking, with the
  ranking left out (`method.ranked: false`).
- **`rank_flood_products` (Module 7)** takes `weights` -- the user's own FINAL
  weights by criterion id. Unnamed criteria keep their computed weight; the set
  is renormalised; `weights_source` and `weights_changed` record what moved,
  and the Module 7 document opens with "The weights you set". With an
  `output_dir` it draws `topsis_closeness.png`, the closeness meter (anti-ideal
  0 to ideal 1, one arrow per product); library callers pass none and draw
  nothing.
- Thesis path: "Scores and weights" (Module 6) then "Ranking" (Module 7); a
  ranking run also counts as Module 6.
- Criteria renamed the same day: T1-T3 -> **L1-L3** (land characteristics,
  group "Land characteristics"); P1 **Spatial resolution**, P4 **Uncertainty
  layer**, P5 **Archive length**. Results stored before keep their T ids.

