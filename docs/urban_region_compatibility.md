# Urban Region Compatibility

Why `fetch_lulc_data` classifies built-up surface on **BUI**, and returns NDBI
alongside it rather than instead of it.

For the case against BUI -- where it stops working -- see
[arid_region.md](arid_region.md).

**TODO(arid-bui):** the argument below stands for a vegetated AOI, but the
overall direction for BUI is unsettled and the index is unvalidated over bare
ground. See [arid_region.md](arid_region.md).

## The problem with NDBI on its own

```
NDBI = (SWIR16 - NIR08) / (SWIR16 + NIR08)          Zha et al. 2003
```

NDBI is positive wherever a surface is brighter in SWIR than in NIR. Concrete,
asphalt and roofing are, which is the point. But so is anything dry and
mineral:

- bare and ploughed soil
- dry riverbed and exposed gravel bar
- sand
- harvested and fallow cropland
- rock outcrop

For a flood model this list is not a footnote. A dry riverbed sits inside the
exact channel the model cares about, and fallow cropland surrounds it. Both
come out of NDBI as "built-up", and both behave nothing like a city under
rainfall.

Worse, the threshold that separates built-up from bare soil is not a constant.
It shifts with soil type, moisture, illumination and season, so a value tuned on
one scene does not transfer to the next one -- and our scene selection
deliberately reaches back up to 90 days for a cloud-free image, so consecutive
runs over one AOI may well land in different seasons.

## What BUI adds

```
BUI = NDBI - NDVI                                    Zha et al. 2003
```

Subtracting NDVI brings a third band (red) and a second, independent physical
property (photosynthetic activity) into a decision that NDBI makes on SWIR
brightness alone. The two errors it removes are the ones that matter in a
populated catchment:

- **Vegetated surfaces that are bright in SWIR.** Dry-season canopy, senescent
  crops and stressed vegetation can push NDBI positive. Their NDVI is high, so
  BUI drives them firmly negative.
- **Mixed pixels.** At 20 m, most of a real city block is part roof, part road,
  part tree. NDBI reads the mixture as ambiguous and lands near zero; BUI's
  vegetation term resolves which way the mixture leans instead of leaving it on
  the threshold.

The separation is large enough not to be threshold-sensitive. On representative
reflectances, forest lands at BUI -1.33 and built-up at +0.04 -- a gap of 1.37,
against NDBI's 0.57 for the same pair. That is the whole argument: BUI is
chosen not because it is a better built-up detector in isolation, but because
its **margin against vegetation** is wide enough to survive a threshold that
was not tuned on this scene.

BUI also spans [-2, 2] rather than [-1, 1], since it is a difference of two
normalised differences. `fetch_lulc_data` histograms it over the wider range;
do not assume the [-1, 1] bounds that hold for every other index here.

## Why NDBI is still returned

Three reasons, all practical:

1. **BUI is not independent of it.** BUI is defined as NDBI minus NDVI, so
   returning only BUI hides one of its two terms. When a BUI value is
   surprising, the only way to tell whether the anomaly came from the built-up
   term or the vegetation term is to look at both.
2. **NDBI is what the literature reports.** Comparing this pipeline against a
   published study of the same city usually means comparing NDBI.
3. **Cheap.** Both terms are already computed, so returning both costs one more
   array in a dict.

The same reasoning is why NDMI is returned even though it is, on the same band
pair, the algebraic negative of NDBI. See the band-pairing note in
[fetch_lulc_data.md](fetch_lulc_data.md).

## What BUI still does not give you

BUI is a **surface** index. It answers "how much of this AOI is impervious
built-up material", which drives the runoff coefficient. It does not answer:

- how many buildings there are
- how tall they are, or how many floors are exposed below a given flood depth
- how many people are inside them
- whether a given structure is residential, industrial or critical
  infrastructure

Those are the questions a city planner actually asks Citadel, and no
normalised difference of two reflectance bands can answer any of them. A 20 m
pixel that is 80% impervious is the same BUI value whether it holds one
warehouse or forty houses.

## Future work

**TODO: building count.** Impervious fraction is a proxy for exposure; building
count and footprint are exposure. Candidate routes, none committed:

- **Open Buildings / Microsoft Building Footprints** -- pre-computed global
  footprint polygons. Cheapest by far: a spatial query returns count, total
  footprint area and a size distribution with no imagery processing at all.
  This is the obvious first move.
- **OpenStreetMap building polygons** -- free and editable, but coverage is
  wildly uneven and the gaps correlate with exactly the informal settlements
  that flood worst. Dangerous as a sole source.
- **Segmentation on higher-resolution imagery** -- Sentinel-2 at 10 m cannot
  resolve individual buildings in dense fabric; this needs sub-metre commercial
  imagery and a model. Real work, real cost.
- **Building height** from Sentinel-1 or from shadow length, which is what turns
  a count into depth-damage exposure.

TODO: once footprints exist, cross-check them against the BUI impervious
fraction over the same AOI. Two independent estimates of built-up extent that
disagree are more informative than either alone, and the disagreement is a
signal Entro can use.
