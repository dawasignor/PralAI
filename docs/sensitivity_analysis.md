# `sensitivity_analysis`

Sweeps each criterion weight of the NRT flood-product ranking across its whole
range, one at a time, and reports the smallest change that alters the order.
Feeds thesis output 5.

**It fetches nothing and it costs nothing.** Like `rank_flood_products` it
reaches no catalogue and no bucket; unlike it, it does not even consume a
payload -- it takes the same five event indices and re-runs the ranking itself.

Author's construction. **The specification describes no sensitivity
procedure**: its steps 8 and 9 are a weighted-sum robustness check and a
Spearman comparison *between methods*, which is a different question and is
still not implemented. Nothing here closes that gap.

## Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `cloud_pct` | float | yes | | AOI cloud cover for the event, 0-100. |
| `wind_ms` | float | yes | | AOI wind speed, m/s, >= 0. |
| `veg_pct` | float | yes | | Share in the dense-vegetation class, 0-100. |
| `steep_pct` | float | yes | | Share classified steep or very steep (slope >= 15°, Horn's method), 0-100. |
| `urban_pct` | float | yes | | Share above the BUI threshold, 0-100. |
| `mode` | str | no | `WEIGHT` | Passed to `rank_flood_products`. |
| `base_weights` | dict/list | no | equal (1/12) | Passed to `rank_flood_products`. |
| `parameters` | dict | no | | Passed to `rank_flood_products`. |
| `grid_step` | float | no | `0.005` | Spacing of the bracketing grid, in normalised weight units. |
| `report_step` | float | no | `0.1` | Spacing of the printed response curves. Reporting only; no threshold comes from it. |
| `tolerance` | float | no | `1e-9` | Bisection tolerance for a threshold, in weight units. |

**It takes the indices, not a ranking payload.** A payload handed in by hand
can be from another event, another parameter set or an older run, and a
threshold measured against the wrong baseline is wrong in a way no reader of
output 5 could detect. Taking the indices makes that impossible, and the
recomputed baseline is checked against the ranking it produced before any
sweep runs -- a closeness drift above 1e-9 raises.

## What is perturbed, and what is not

**Weights only.** The product scores, the contested evidence cells, the
unresolved P2 revisit range, the author parameters and the five indices
themselves are all held where output 3 records them. A ranking that survives
every weight in output 5 has **not** been shown to survive a different score,
and the output says so in its own words rather than leaving it to be inferred.

## The construction

Criterion *j*'s normalised weight is set to *t*; the other eleven are rescaled
by `(1 - t) / (1 - w_j)`, so their ratios are untouched and the twelve still
sum to 1.

**Module 8 sweeps around Module 7's weights** (user decision 2026-11-22): the event-first weights and any weight or score the user changed at Module 6 or 7, read from the stored ranking. Every threshold comes with the weight to state ("set L1 to ..."), `equivalent_stated_weight` = t (1 - w) / (1 - t), which puts the criterion exactly at the threshold when stated to the ranking. No criterion is unreachable any more: under event-first weighting none is zero, and any weight can be stated.

**That is exactly what stating one weight does.** `rank_flood_products(weights=
{j: s})` keeps the other eleven in proportion and renormalises, so the sweep
family is the set of rankings a user can ask for one weight at a time. The test
suite checks every `equivalent_stated_weight` by stating it and confirming the
threshold comes back out. A value above 1 is meaningful rather than out of
range: that criterion must outweigh all the others together.

Until 2026-11-22 the sweep was read as a sweep of one BASE weight under the
multiplicative rule, and a zero modulator (W2 over Kelani) made a criterion
unreachable. Neither holds under event-first weighting.

## How a threshold is found

Closeness is not linear in a weight -- the distances are Euclidean, so the
response curves bend -- and there is no closed form for a crossing. Each
threshold is bracketed on the grid and bisected to `tolerance`.

**The predicate is the whole rank vector**, not one pair: any change of order
counts, which is the conservative reading.

**A total collapse is not a reordering.** At `t = 1` on a criterion where the
candidates are identical, TOPSIS discriminates nothing and every alternative
shares rank 1. That is the absence of a ranking, not a different one, and it
is excluded from the predicate -- otherwise every such criterion would report
a spurious threshold at 1.0. A *partial* tie still counts: two alternatives
that were 2 and 3 and are now both 2 have genuinely stopped holding their
order.

Both ends are searched. `t = 0` deletes the criterion and `t = 1` gives it the
whole decision; neither is trimmed, because "this criterion cannot reorder the
ranking anywhere in [0, 1]" is the strongest stability result available and it
only exists if the ends are reached.

**A crossing narrower than `grid_step` is invisible.** An order that changes
and changes back inside one step reads as no change at all. `grid_step` is
reported in the payload and in output 5 so that limit is visible rather than
implied.

## Rank agreement

Every perturbed ranking on the search grid is compared with the baseline by
**Spearman's rho** and **Kendall's tau-b**, computed on the closeness values
with ties averaged. Both are written out in this module rather than imported:
scipy is not a dependency of this repo, and a rank correlation over five
alternatives is not worth becoming one.

Tie handling is not decoration. TOPSIS ties exactly at a crossing, which is
where this analysis looks, and a formula that assumes distinct ranks returns a
number that is quietly wrong precisely there. In tau-b a pair tied on **both**
sides belongs to `n1` and to `n2` and so cancels in both factors of the
denominator; counting it twice makes tau of a tied vector with itself come out
at 0.833 instead of 1. That error was in the first version of this module and
`tests/test_sensitivity.py` section 2 is what caught it.

The reported fractions -- *order unchanged*, *leader unchanged* -- are **shares
of the weight range, not probabilities**. Nothing here says a weight is
likely, only that it is possible. Weighting the space by plausibility is
output 6's job, not this one's.

## Outputs

`core/mcdm_report.py::sensitivity_markdown` renders output 5, deterministically
and with no agent writing, for the same reason as outputs 3 and 4: a threshold
is a number produced by a bisection against a stated predicate, and a
paraphrase of it is not the same claim.

Five sections: the thresholds, the leader changes, the rank agreement, the
response curves, and what the analysis does not test.

## The figure

`core/charts.py::stability_bands` draws one figure into the event's
`figures/` directory and gives it a row in `figures/index.json`, the same
convention the raster figures follow. **Since 2026-11-02 the tool draws it itself** (`output_dir`, `event_id`, `write_figure`; returned as `figure`, which the console shows) **and Module 8 embeds it** (`sensitivity_figure`). Before that, Output 5 did not embed it -- the
document stays text, as output 2 does, and the index is the manifest.

It is a redraw, not an analysis. Every band edge is read from the cached
payload output 5 was rendered from, so the picture and the tables cannot
disagree; `tests/test_sensitivity.py` section 13 checks each drawn edge back
against its threshold.

One row per criterion on a fixed 0-1 weight axis, with two nested bands: the
light one is where the top-ranked alternative keeps its place, the dark one
inside it is where the whole order holds. Rows are sorted narrowest-first,
except that **unreachable criteria go last** -- a criterion no base weight can
move is not the weak link, whatever its band width, and a reader scans from
the top.

The nesting is asserted, not assumed. Every change of leader is also a change
of order, so the inner band must lie inside the outer one; `stability_rows`
raises if it does not, because the region would then not be the single
interval the drawing represents. Over the Kelani event the order-preserving
region is one contiguous interval for all twelve criteria -- measured, not
assumed.

The **half and double ticks** are a sense of scale, not a range of defensible
weights: nothing in this project sources one, and the figure does not invent
it. Their job is to stop a full 0-1 axis making every band look reassuringly
wide.

## Limits worth stating before citing a result

- **One weight at a time.** Two criteria moving together can cross a threshold
  that neither crosses alone, and this construction cannot see it. That is
  what output 6 (SMAA) is for -- it samples the weight space rather than
  twelve lines through it.
- **Most of the swept range is not a plausible opinion.** A weight of 0.9 on
  one criterion is a different decision model. Read the thresholds, which are
  local; the fractions are context.
- **One method.** Only TOPSIS exists here, so this measures how stable the
  TOPSIS order is, not how stable the decision is.

## Verification

`tests/test_sensitivity.py`, offline, twelve checks. The one that matters most
is section 4: a two-by-two problem with an identity matrix, where

    Ci(A1) = w1 / (w1 + w2)

so the crossing is at exactly `w1 = 0.5` and the bisected threshold can be
compared with the closed form rather than with a previous run of the same
code. Sections 7 and 8 check every real threshold brackets a genuine change of
order and that its equivalent base weight reproduces it through
`rank_flood_products`.

## A note on dtypes

`mcdm.topsis` coerces its matrix, weights and directions on the way in (fixed
2026-09-22). It used to inherit the input dtype through
`np.zeros_like(matrix)`, so an integer matrix raised a casting error instead of
normalising, and every caller had to remember `dtype=float`. The raw scores
genuinely are integers -- 250 m posting, 12 days, a 23-year archive -- and only
normalisation puts them in 0-1, so the coercion belongs at the one function
every flow passes through rather than at each call site.
