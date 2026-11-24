# ARID Region

> **TODO(arid-bui): this needs a better direction, and nothing downstream
> should be built on BUI until it has one.**
>
> What is settled: BUI cancels vegetation confusion, not bare-soil confusion,
> so over sand it is close to a no-op and reports built-up surface that is not
> there. What is not settled is the answer -- the four options in *What to do
> instead* below have never been chosen between, and none has been tried. The
> index is also unvalidated outside one humid AOI.
>
> Every `TODO(arid-bui)` in the repo points here. Decide the direction here
> first; the other flags are only the places that follow from it.

Why the built-up index behaves differently over desert, and what it does and
does not fix. Read `docs/urban_region_compatibility.md` first for the case in
favour of BUI; this document is the boundary of that case.

## The short version

BUI removes **vegetation** confusion from NDBI. It does **not** remove
**bare-soil or sand** confusion, and over dunes it is close to a no-op. An arid
AOI will report built-up surface that is not there, and no threshold on BUI
alone will fix it.

This is stated plainly because the opposite is easy to assume: BUI is the
improved index, so it ought to improve this too. It does not, and the reason is
in the arithmetic.

## Why the correction cancels

```
NDBI = (SWIR16 - NIR08) / (SWIR16 + NIR08)
BUI  = NDBI - NDVI
```

The subtraction only does work where NDVI is non-zero. Its whole job is to
push down surfaces that are bright in SWIR *and* green -- the vegetated false
positives NDBI picks up. Sand is not green. Its NDVI sits near zero, so
`BUI ~= NDBI - 0 ~= NDBI`. **The correction switches itself off exactly on the
surface that needed correcting.**

Worked through with representative surface reflectances:

| Surface | Red | NIR | NIR08 | SWIR16 | NDVI | NDBI | **BUI** |
|---|---|---|---|---|---|---|---|
| Closed-canopy forest | 0.03 | 0.40 | 0.42 | 0.15 | 0.86 | -0.47 | **-1.33** |
| Concrete / built-up | 0.22 | 0.25 | 0.26 | 0.32 | 0.06 | 0.10 | **+0.04** |
| Sand dune | 0.35 | 0.40 | 0.42 | 0.50 | 0.07 | 0.09 | **+0.02** |

Forest separates from built-up by 1.37 BUI units. Sand separates from built-up
by 0.02 -- inside the noise of atmospheric correction, let alone of a
scene-to-scene threshold. Dune and concrete are not distinguishable on this
axis, because on a normalised SWIR/NIR difference they are genuinely similar
targets: both are bright, dry, mineral and unvegetated.

## The literature

This is a known and documented failure, not an artefact of our implementation.
As-syakur et al. (2012) introduced the Enhanced Built-Up and Bareness Index
specifically because the NDBI family cannot separate built-up surface from bare
land, and they build the index around a thermal band to do it -- an
acknowledgement that the information needed is not present in the SWIR/NIR pair
at all. Xu (2008) makes the same argument from a different direction with the
Index-based Built-up Index: the fix is to combine built-up, vegetation and
water indices, not to threshold one harder.

Both are in [SOURCES.md](SOURCES.md).

## What this means for a flood model

The failure is not symmetric, and its direction matters.

Built-up surface and bare sand have **opposite** hydrological behaviour.
Concrete is impervious: it sheds nearly all rainfall to runoff. Dune sand is
among the most permeable surfaces there is, with infiltration rates that can
absorb most of a storm. Misreading dune as built-up therefore does not blur the
runoff estimate -- it **inverts** it, and it inverts it upward, toward more
predicted flooding. In an arid catchment this is the difference between a wadi
that floods flashily because of terrain and one that is predicted to flood
because of a city that does not exist.

So for an arid AOI, treat the BUI built-up fraction as an **upper bound**, not
an estimate.

## What to do instead, in increasing order of effort

1. **Report it, do not silently use it.** `fetch_lulc_data` returns full BUI
   statistics and a histogram rather than a single built-up number, so a
   bimodal or implausibly wide distribution is visible to Entro.
2. **Mask by slope.** Dune fields and built-up areas differ in terrain
   signature. `fetch_terrain_data` is already a separate workflow returning a
   slope raster on request; an aeolian dune field has a characteristic slope
   texture that flat urban fabric does not.
3. **Bring in a thermal band.** This is what EBBI does. Sentinel-2 has none, so
   it means Landsat 8/9 TIRS -- already in the same Earth Search catalogue as
   `landsat-c2-l2`, at 30 m and 16-day revisit.
4. **Use a labelled land-cover product** and skip the index threshold entirely.

## Status

Not validated on an arid AOI. Everything above is derived from the index
definitions and the cited literature; the only region this repo has run over is
Shillong (humid subtropical, BUI mean -0.72, 53% dense vegetation), which
exercises the vegetated end of the range and not the arid end. Before trusting
BUI over a desert catchment, run it somewhere known and check the histogram.

TODO: pick an arid validation AOI and record the BUI distribution here.
