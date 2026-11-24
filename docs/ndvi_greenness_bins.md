# NDVI Greenness Bins

What `fetch_lulc_data` classifies today, why the bins are named for greenness
rather than for land cover, and the temporal method that turns them into a
real forest / shrub / cropland split.

---

## 1. What NDVI measures

```
NDVI = (NIR - Red) / (NIR + Red)
```

Chlorophyll absorbs red light and leaf mesophyll scatters near-infrared, so the
gap between the two bands tracks **photosynthetic biomass** -- how much living
green tissue a pixel contains (Rouse et al. 1974; Tucker 1979).

That is the whole of it. NDVI is a measure of greenness at one instant. It
carries no information about plant height, woodiness, canopy structure,
management, or whether anybody planted what is growing there.

## 2. Why the bins are not land-cover labels

The bins implemented in `sentinel.py::NDVI_BINS` are:

| Code | Name | NDVI |
|---|---|---|
| 1 | `non_vegetation` | < 0.19 |
| 2 | `low_greenness` | 0.19 - 0.40 |
| 3 | `moderate_greenness` | 0.40 - 0.50 |
| 4 | `high_greenness` | 0.50 - 0.60 |
| 5 | `very_high_greenness` | >= 0.60 |

**Breaks since 2026-11-19: Aryal, Sitaula and Aryal (2022)**, *Land* 11, 351,
doi:10.3390/land11030351 -- their Level-3 Sentinel-2 thresholds: 0.19
non-vegetation / vegetation (Table 2), 0.50 shrub / trees (Table 3), 0.40 and
0.60 stressed / healthy (Tables 4 and 5). Named by greenness, not shrub / trees,
for the reason below. The 0.60 break is unchanged, so the dense share the
ranking reads (L1) is the same quantity; results stored before name that class
`dense_vegetation`. The breaks before were 0 / 0.15 / 0.35 / 0.60 with no paper
behind them (classes `water_or_wet`, `bare_or_built`, `sparse_vegetation`,
`moderate_vegetation`, `dense_vegetation`); the figures below were measured
with those.

They are deliberately **not** called Forest, Shrub and Agriculture, because a
single-date NDVI threshold cannot produce those classes. In the growing season
a closed-canopy forest and a dense crop canopy both sit near 0.8. They are
the same number. No threshold separates identical values, and a classifier that
emitted "Forest" for a cropland pixel would be wrong in a way that looks
entirely plausible in a table -- which is worse than being obviously wrong.

The live Shillong scene shows the collapse directly: **53.5% `dense_vegetation`
and 33.9% `moderate_vegetation`**, one undifferentiated mass of green covering
87% of the AOI. Meghalaya has forest, shrub, jhum fallow and cropland inside
that mass, and one date cannot tell them apart.

So the bins are honest about their axis. They are useful as-is for the things
greenness genuinely indicates -- vegetation cover fraction, the impervious
complement, and NDVI's role in `BUI = NDBI - NDVI` -- and they carry a
`caveat` field in the returned JSON saying so.

## 3. What separates the classes: time, not thresholds

The discriminating information is **phenology** -- how greenness changes across
a year. This is long-established: DeFries & Townshend (1994) built global land
cover from NDVI temporal profiles precisely because the seasonal *shape*
separates classes that any single date confounds.

The three signatures differ in a way that is hard to confuse:

| Class | Mean NDVI | Seasonal amplitude | Shape |
|---|---|---|---|
| Evergreen forest | high | **low** | flat all year |
| Shrub / grassland | moderate | moderate | broad, gentle rise |
| Cropland | moderate-high | **high** | sharp peak, crash at harvest |

Cropland's signature is the distinctive one: it returns to near-bare soil after
harvest, so it swings across almost the full NDVI range within one year. Forest
never does. That single contrast -- amplitude, not level -- carries most of the
separation.

## 4. The method

Two numbers per pixel, from a year of scenes:

```
mean_ndvi       = mean NDVI over all valid observations
ndvi_amplitude  = p90(NDVI) - p10(NDVI)
```

Percentiles rather than min and max, because a single residual cloud edge or
shadow would dominate a true range. This is the same robust-envelope idea
behind season-extraction methods such as TIMESAT (Jonsson & Eklundh 2002),
reduced to its cheapest possible form: no curve fitting, no smoothing, no
harmonic model.

Decision rule:

```
high amplitude                  -> cropland      (bare after harvest)
low amplitude  + high mean      -> forest        (evergreen, stable)
low amplitude  + moderate mean  -> shrub / grass
low mean, any amplitude         -> bare / built  (falls through to BUI)
```

**The cut points must be calibrated per region and recorded here.** Amplitude
thresholds are not transferable: a monsoon-driven catchment and a temperate one
have different baseline seasonality, and deciduous forest has real amplitude of
its own. Publishing a number here that was tuned on Meghalaya and reused in
Kerala is exactly the failure this document exists to prevent. Calibrate
against a labelled sample, write the values into this file with the AOI they
came from, and treat them as local.

### Why this fits the existing design

- **No new dependency, no new source, no training data.** It is the NDVI
  already computed, over more scenes.
- **It inverts the cloud problem.** Section 3 of
  [fetch_lulc_data.md](fetch_lulc_data.md) documents that the pre-event window
  is the worst window Sentinel-2 has -- over Shillong, zero scenes under 70%
  cloud in 15 days. But the annual curve does not need pre-event imagery. It
  wants the *dry season*, which over the same AOI gave **13 of 16 scenes under
  20% cloud**. The clear imagery that is useless as an event baseline is
  exactly the imagery this method needs.
- **Land cover is slow.** A phenology profile is a property of the catchment,
  not of the event, so it is computed once per AOI per year and cached -- the
  same argument that made `fetch_terrain_data` a separate function.

### Implementation shape

Not built. It requires `fetch_lulc_data` to composite across a list of scenes
rather than select one, which is a change to the scene-selection contract:

1. Search a full year, keep every scene passing a relaxed `min_valid_fraction`.
2. Per scene, compute NDVI on the shared 20 m grid, SCL-masked.
3. Stack and reduce to `mean_ndvi`, `ndvi_amplitude` and an observation count.
4. Apply the calibrated rule; write a `uint8` class raster and a legend.
5. Carry the per-pixel observation count through as the confidence measure --
   the temporal equivalent of `valid_fraction`, and equally load-bearing. A
   pixel with four cloud-free looks in a year has an amplitude estimate that
   should not be trusted like one with twenty.

Suggested separate workflow, `fetch_vegetation_phenology`, rather than more
parameters on `fetch_lulc_data`: different cadence, different window, different
failure modes.

---

## Sources

- **Rouse, J. W., Haas, R. H., Schell, J. A., & Deering, D. W. (1974).**
  "Monitoring vegetation systems in the Great Plains with ERTS."
  *Third ERTS Symposium*, NASA SP-351, 309-317.
  The original definition of NDVI.

- **Tucker, C. J. (1979).** "Red and photographic infrared linear combinations
  for monitoring vegetation." *Remote Sensing of Environment*, 8(2), 127-150.
  Establishes what the red/NIR combination physically measures -- green
  biomass -- which is the basis for naming these bins after greenness.

- **DeFries, R. S., & Townshend, J. R. G. (1994).** "NDVI-derived land cover
  classifications at a global scale." *International Journal of Remote
  Sensing*, 15(17), 3567-3586.
  Land cover from NDVI *temporal profiles* rather than single-date values --
  the core argument of section 3.

- **Jonsson, P., & Eklundh, L. (2002).** "Seasonality extraction by function
  fitting to time-series of satellite sensor data." *IEEE Transactions on
  Geoscience and Remote Sensing*, 40(8), 1824-1832.
  The TIMESAT season-extraction method. The amplitude metric in section 4 is a
  deliberately reduced form of it.

- **Reed, B. C., Brown, J. F., VanderZee, D., et al. (1994).** "Measuring
  phenological variability from satellite imagery." *Journal of Vegetation
  Science*, 5(5), 703-714.
  Defines the phenological metrics -- amplitude, onset, duration -- that
  distinguish cover types.

All are also listed in [SOURCES.md](SOURCES.md).
