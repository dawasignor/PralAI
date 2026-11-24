# SOURCES

Every dataset and every published method the workflows in `core/workflows/`
depend on, plus the candidate sources not yet used.

Rule: **nothing gets computed in this repo without an entry here.** If you add
an index, a threshold or a dataset, add its reference before you merge. A
number in a thesis needs a provenance chain, and this file is that chain.

## How an entry is stored

Four fields per source:

| Field | Means |
|---|---|
| **Source** | The citation. |
| **Context** | What this source is being relied on *for*, in this project. |
| **Vault note** | The note in the knowledge vault (`<VAULT_ROOT>`) that carries the reasoning. `TOTAG` when no note exists yet -- a backlog marker, not an error. |
| **Code** | The module, function or logic the source justifies. `--` when the source is cited but nothing computes from it yet. |

A source with a `TOTAG` vault note and a filled `Code` field is the dangerous
combination: the repo is computing something whose reasoning is not written
down anywhere.

**Record the vintage, and quote it with the figure.** The vintage is the date
of the data or the publication -- not the date it was read. Sources are
reissued: a national inventory, an index release, a fact sheet, a dataset
version. A figure quoted without its date does not stay right and then go
wrong visibly; it ages quietly into a false claim while still looking sourced.
So the rule is two-sided:

- Every entry states its vintage, and says so where the release is periodic
  (annual index, periodic update).
- Before quoting any figure from this file, check whether a newer release
  exists. If there is one, use it and update the entry.

Section 7 carries the vintage as its own column. Older sections state it in
the citation, which is enough for a paper with a fixed publication year but
not for a dataset or an inventory.

---

## 1. Datasets

| Source | Context | Vault note | Code |
|---|---|---|---|
| Drusch, M., Del Bello, U., Carlier, S., et al. (2012). "Sentinel-2: ESA's Optical High-Resolution Mission for GMES Operational Services." *Remote Sensing of Environment*, 120, 25-36. | The mission reference for the optical source behind every land-cover index: bands, revisit, resolution. | `[[Sentinel-2 C1 L2A]]` | `core/workflows/sentinel.py` |
| ESA. *Sentinel-2 Level-2A Algorithm Theoretical Basis Document*, S2-PDGS-MPC-ATBD-L2A. | Defines the Scene Classification Layer. The classes treated as real surface observation (4, 5, 6, 7, 11) come from here. | `[[Scene usability - SCL valid fraction]]` | `sentinel.py` scene selection; the SCL probe and `valid_fraction` |
| European Space Agency & Airbus (2022). *Copernicus DEM -- Global and European Digital Elevation Model*. ESA, doi:10.5270/ESA-c5d3d65. | The 30 m elevation source. Sentinel-2 cannot supply this -- no stereo pair, no DEM product. | `[[Copernicus DEM GLO-30]]` | `core/workflows/terrain.py` |
| Pekel, J.-F., Cottam, A., Gorelick, N., & Belward, A. S. (2016). "High-resolution mapping of global surface water and its long-term changes." *Nature*, 540(7633), 418-422. | The permanent-water baseline. The `occurrence` band and the >=90% permanent / 25-90% seasonal breakpoints are the paper's own, not ours. | `[[JRC Global Surface Water]]` | `core/workflows/water.py` |
| Munoz-Sabater, J., Dutra, E., Agusti-Panareda, A., et al. (2021). "ERA5-Land: a state-of-the-art global reanalysis dataset for land applications." *Earth System Science Data*, 13(9), 4349-4383. | Intended source of actual volumetric soil moisture. Sentinel-2 cannot measure it: no thermal channel, no L-band radar. **Not what `weather.py` receives** (measured 2026-09-26): with no model named, its soil moisture matches ECMWF IFS in 72 of 72 hours at Edinburgh and Tirupati and ERA5-Land in 0. Unfixed. | `[[ERA5-Land via Open-Meteo Archive]]` | `weather.py` (`soil_moisture_surface`, `soil_moisture_root_zone`) -- label only, see Context |
| Hersbach, H., Bell, B., Berrisford, P., et al. (2020). "The ERA5 global reanalysis." *Quarterly Journal of the Royal Meteorological Society*, 146(730), 1999-2049. doi:10.1002/qj.3803. | The reanalysis behind `fetch_weather_grid(model="era5")`: 31 km, served on 0.25 deg, 1940 onwards, about 5 days behind real time. **Never actually used by any cached output** -- every weather payload so far has been IFS (see next row). Metadata checked against the DOI record 2026-09-26. | TOTAG | `weather_grid.py`, `MODELS["era5"]` |
| ECMWF. *IFS Documentation* (Integrated Forecasting System), cycle CY50R1 current as of 2026-09-26. https://www.ecmwf.int/en/publications/ifs-documentation. Served through the Open-Meteo archive, which describes its IFS dataset as "assembled by Open-Meteo using simulation runs daily at 0z, 6z, 12z and 18z, employing the most up-to-date version of IFS": 9 km, hourly, 2017 onwards, updated every 6 h with no delay. | The model behind **every cached thesis weather output to date** -- Kelani outputs 1 and 3-5 and Bangkok output 1 -- and, measured at Edinburgh 2026 and Tirupati 2021, behind `weather.py` for dates from 2017 (before 2017 best_match cannot be IFS; which model answered there is unmeasured). The default of `fetch_weather_grid` since 2026-09-26. **Vintage caveat:** the IFS cycle behind a given hour is not recorded by Open-Meteo, so cite the retrieval date (`meta.source.retrieved_at`) with any figure. ECMWF publishes no citation format for the documentation; this entry cites the page. | `[[ECMWF IFS via Open-Meteo Archive]]` | `weather_grid.py`, `MODELS["ecmwf_ifs"]`; also what `weather.py` receives from 2017, unlabelled |
| Zippenfenig, P. (2023). *Open-Meteo.com Weather API*. doi:10.5281/zenodo.7970649. | The API that redistributes the archive models (ECMWF IFS, ERA5, ERA5-Land). Cite alongside the model, never instead of it -- and name the model, because its default `best_match` "combines IFS HRES, ERA5 and ERA5-Land seamlessly". | `[[ERA5-Land via Open-Meteo Archive]]` | `core/workflows/weather.py`, `weather_grid.py` |
| European Space Agency & Airbus (2022). *Copernicus DEM Product Handbook*, auxiliary Water Body Mask (WBM) layer. ESA, doi:10.5270/ESA-c5d3d65. | Which pixels are sea. Ships beside the GLO-30 tiles, same bucket, same lattice, same resolution. Codes 0 land / 1 ocean / 2 lake / 3 river -- the one distinction that lets the ocean be excluded while the catchment's own rivers and lakes stay in. | `[[Copernicus DEM GLO-30]]` | `core/workflows/landmask.py` |
| Zanaga, D., Van De Kerchove, R., Daems, D., De Keersmaecker, W., Brockmann, C., Kirches, G., Wevers, J., Cartus, O., Santoro, M., Fritz, S., Lesiv, M., Herold, M., Tsendbazar, N.-E., Xu, P., Ramoino, F., & Arino, O. (2022). *ESA WorldCover 10 m 2021 v200*. Zenodo, doi:10.5281/zenodo.7254221. CC BY 4.0. | **The land-cover fallback**: used only when Sentinel-2 fails its 0.7 valid-fraction threshold, for the vegetation share (class 10, tree cover) and the built-up share (class 50). 10 m, Sentinel-1 + Sentinel-2, eleven classes, EPSG:4326 on a 3x3 degree tile grid. **Vintage: one snapshot of 2021** (the map's own time_start/time_end are 2021-01-01 to 2021-12-31). It is spatially accurate but does not address temporal relevance: it describes 2021, not the weeks before the event, so for the MCDM it is a recorded input uncertainty, never a silent substitute. **Not the same quantity as the Sentinel-2 indices:** tree cover is not dense NDVI, built-up is not BUI > 0.2. v100 (2020) and v200 (2021) use different algorithms, so differences between them are not change. Release and record checked 2026-10-25. | TOTAG (written in the docs phase) | `core/workflows/worldcover.py` (`fetch_worldcover`); `mcdm.indices_from_payloads` fallback |
| Tsendbazar, N.-E., Xu, P., Herold, M., Lesiv, M., & Duerauer, M. (2022). *WorldCover Product Validation Report (D12-PVR)*, version 2.0, 2022-10-25. ESA WorldCover consortium, contract 4000128231/19/I-LG. | The accuracy of the 2021 v200 map, quoted in every `fetch_worldcover` uncertainty block. **Global overall accuracy 76.7 +/- 0.5%** (2.16 million SSUs at 21,624 PSUs, Table 2); Asia 82.1 +/- 1.0% (Table 3). Per class, global, user's / producer's accuracy (Table 2, p. 15): tree cover 80.0 +/- 0.7 / 91.9 +/- 0.5; shrubland 49.1 / 46.9; grassland 71.9 / 66.7; cropland 80.6 / 79.3; **built-up 65.9 +/- 3.3 / 73.2 +/- 2.6**; bare/sparse 92.1 / 82.5; snow and ice 93.0 / 99.1; permanent water 89.4 / 86.4; herbaceous wetland 30.5 / 44.6; mangroves 74.1 +/- 13.2 / 46.2 +/- 16.0; moss and lichen 57.5 / 46.4. The report notes a slight global overestimation of trees. Read from the PDF in the product bucket (`v200/2021/docs/WorldCover_PVR_V2.0.pdf`) 2026-10-25. | TOTAG (docs phase) | `worldcover.py` `ACCURACY` table |

### Access and processing notes

Operational detail, not citations.

- **Sentinel-2 C1 L2A** -- Earth Search v1, `https://earth-search.aws.element84.com/v1`,
  collection `sentinel-2-c1-l2a`. Public COGs on AWS S3, `us-west-2`. No
  authentication, no asset signing.
  **Baseline 05.00+ carries a BOA offset of -0.1 alongside the 0.0001 scale.**
  That offset does not cancel out of a normalised difference: the numerator
  cancels, the denominator does not. `sentinel.py::_asset_scaling` reads both
  from the STAC item's `raster:bands` rather than hardcoding them.
- **Copernicus DEM GLO-30** -- `https://copernicus-dem-30m.s3.amazonaws.com`,
  AWS Open Data. One COG per 1-degree tile, anonymous, not requester-pays.
  Derived from TanDEM-X, 2011-2015 acquisition.
- **Copernicus DEM Water Body Mask** -- same bucket, inside each tile's folder
  at `<tile>_DEM/AUXFILES/<tile>_WBM.tif`. uint8, identical grid to the DEM.

  **The class codes were verified against the data, not taken from the
  handbook.** In tile `N06_00_E079_00`, code 1 is 100% of every longitude band
  west of 79.83 -- open Indian Ocean -- with a mean elevation of exactly
  0.00 m, while code 0 appears only in the easternmost band where Sri Lanka's
  coast is; codes 2 and 3 are small, inland and near sea level. A legend that
  agrees with geography *and* with a second variable is one worth relying on.

  Used to exclude the sea from every AOI statistic. **Only code 1 is excluded
  by default** -- a lake and a river are part of a catchment, and the Kelani's
  own channel is code 3.
- **JRC GSW v1.4 (2021 release)** --
  `https://storage.googleapis.com/global-surface-water/downloads2021/`, the
  JRC's own bucket. Plain HTTPS with range requests, no account. 1984-2021
  Landsat, 30 m.
- **Open-Meteo archive** -- `https://archive-api.open-meteo.com/v1/archive`,
  no key. **Name the model** with `models=`; the default `best_match` is chosen
  server-side. `ecmwf_ifs` 9 km hourly, 2017 onwards, no delay; `era5` 0.25 deg,
  1940 onwards, about 5 days behind (nulls, not an error, for the gap);
  `era5_land` 0.1 deg. `end_date` may not pass today in UTC. Verified against
  the Open-Meteo Historical Weather API page 2026-09-26.
- **ERA5-Land** -- has never actually been received by either weather
  workflow; see the ERA5-Land row above.
- **ESA WorldCover 2021 v200** --
  `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/`
  (`s3://esa-worldcover`, eu-central-1), anonymous HTTPS with range requests.
  One COG per 3x3 degree tile, named for its SOUTH-WEST corner
  (`ESA_WorldCover_10m_2021_v200_N06E078_Map.tif` covers 6-9N, 78-81E).
  uint8, nodata 0, EPSG:4326, 1/12000 deg (about 9.3 m at the equator),
  pixel-is-AREA with its origin on the whole degree, overviews 2-64. The
  legend is embedded in the file's tags and was read from there. Checked live
  2026-10-25 (one tile opened in 1.2 s).

---

## 2. Spectral indices

All are normalised differences on Sentinel-2 surface reflectance. Band pairing
is not arbitrary -- see `docs/fetch_lulc_data.md`.

| Source | Context | Vault note | Code |
|---|---|---|---|
| Aryal, J., Sitaula, C., & Aryal, S. (2022). "NDVI Threshold-Based Urban Green Space Mapping from Sentinel-2A at the Local Governmental Area (LGA) Level of Victoria, Australia." *Land*, 11(3), 351. doi:10.3390/land11030351. In the user's Zotero; read 2026-11-19 (Tables 2-5). | **The NDVI class breaks** since 2026-11-19: 0.19 (non-vegetation / vegetation, Table 2), 0.50 (shrub / trees, Table 3), 0.40 and 0.60 (stressed / healthy, Tables 4-5). Classes named by greenness here, not shrub / trees. | `[[NDVI greenness bins]]` | `sentinel.py::NDVI_BINS` |
| Ozdemir, A. (2009). "Landslide susceptibility mapping of vicinity of Yaka Landslide (Gelendost, Turkey) using conditional probability approach in GIS." *Environmental Geology*, 57(7), 1675-1686. doi:10.1007/s00254-008-1449-z. In the user's Zotero; read 2026-11-19 (Table 1). | **Not used since 2026-11-21.** Its 5° slope bands (and steep >= 30°) were the slope classes from 19 to 21 Nov 2026; the user reverted to flat / gentle / moderate / steep / very steep (2 / 5 / 15 / 30°) on Horn's (1981) slope. Kept here because results stored in those days carry its bands. | -- | (history only) |
| Rouse, J. W., Haas, R. H., Schell, J. A., & Deering, D. W. (1974). "Monitoring vegetation systems in the Great Plains with ERTS." *Third ERTS Symposium*, NASA SP-351, 309-317. | NDVI. (`nir` - `red`) / (`nir` + `red`). | `[[NDVI]]` | `sentinel.py`, `ndvi` |
| Zha, Y., Gao, J., & Ni, S. (2003). "Use of normalized difference built-up index in automatically mapping urban areas from TM imagery." *International Journal of Remote Sensing*, 24(3), 583-594. | Source of **both** NDBI and the NDBI-minus-NDVI built-up formulation (BUI). | `[[BUI over NDBI - built-up index accuracy]]` | `sentinel.py`, `ndbi` and `bui` |
| Gao, B.-C. (1996). "NDWI -- A normalized difference water index for remote sensing of vegetation liquid water from space." *Remote Sensing of Environment*, 58(3), 257-266. | NDMI. (`nir` - `swir16`) / (`nir` + `swir16`). | `[[NDMI]]` | `sentinel.py`, `ndmi` |
| Xu, H. (2006). "Modification of normalised difference water index (NDWI) to enhance open water features in remotely sensed imagery." *International Journal of Remote Sensing*, 27(14), 3025-3033. | MNDWI. Open-water enhancement using green/SWIR. | `[[MNDWI]]` | `sentinel.py`, `mndwi` |
| Wang, L., & Qu, J. J. (2007). "NMDI: A normalized multi-band drought index for monitoring soil and vegetation moisture with satellite remote sensing." *Geophysical Research Letters*, 34, L20405. | NMDI. Defined for soil and canopy, where the 1.6 um band is brighter than the 2.2 um band -- which is why pixels with `SWIR16 - SWIR22 <= 0.01` are masked rather than clipped. | `[[NMDI]]` | `sentinel.py`, `nmdi` and its mask |

---

## 3. Vegetation classification from NDVI phenology

The method for separating forest, shrub and cropland: chosen and documented,
**not yet implemented**. Cited in `docs/ndvi_greenness_bins.md`.

| Source | Context | Vault note | Code |
|---|---|---|---|
| Tucker, C. J. (1979). "Red and photographic infrared linear combinations for monitoring vegetation." *Remote Sensing of Environment*, 8(2), 127-150. | What red/NIR physically measures: green biomass. The basis for naming the bins after greenness rather than after land cover. | `[[NDVI greenness bins]]` | `sentinel.py` class legend (`dense_vegetation`, not `forest`) |
| DeFries, R. S., & Townshend, J. R. G. (1994). "NDVI-derived land cover classifications at a global scale." *International Journal of Remote Sensing*, 15(17), 3567-3586. | Land cover from NDVI *temporal profiles* rather than single-date thresholds. | `[[NDVI greenness bins]]` | -- |
| Jonsson, P., & Eklundh, L. (2002). "Seasonality extraction by function fitting to time-series of satellite sensor data." *IEEE TGRS*, 40(8), 1824-1832. | TIMESAT. The amplitude metric planned here is a reduced form of it. | `[[NDVI greenness bins]]` | -- |
| Reed, B. C., Brown, J. F., VanderZee, D., et al. (1994). "Measuring phenological variability from satellite imagery." *Journal of Vegetation Science*, 5(5), 703-714. | Defines amplitude, onset and duration as the metrics that distinguish cover types. | `[[NDVI greenness bins]]` | -- |

---

## 4. Built-up index limitations

Cited in `docs/arid_region.md` and `docs/urban_region_compatibility.md`.

**TODO(arid-bui):** these sources describe the failure; none of them has been
adopted as the fix. See `docs/arid_region.md`.

| Source | Context | Vault note | Code |
|---|---|---|---|
| As-syakur, A. R., Adnyana, I. W. S., Arthana, I. W., & Nuarsa, I. W. (2012). "Enhanced Built-Up and Bareness Index (EBBI) for Mapping Built-Up and Bare Land in an Urban Area." *Remote Sensing*, 4(10), 2957-2970. | NDBI-family indices fail to separate built-up surface from bare land: the arid-region failure mode. | `[[BUI over NDBI - built-up index accuracy]]` | Documented limitation of `bui`; no code change |
| Xu, H. (2008). "A new index for delineating built-up land features in satellite imagery." *International Journal of Remote Sensing*, 29(14), 4269-4276. | IBI. The argument for combining built-up, vegetation and water indices rather than thresholding one. | `[[BUI over NDBI - built-up index accuracy]]` | Rationale for returning `bui` alongside `ndbi` |

---

## 5. Algorithms

**Unverified: the three rank-correlation and sensitivity references added
2026-09-22 carry page numbers taken from secondary citation, not checked
against the originals.** The formulas themselves are checked, against closed
forms, in `tests/test_sensitivity.py`.

| Source | Context | Vault note | Code |
|---|---|---|---|
| Horn, B. K. P. (1981). "Hill shading and the reflectance map." *Proceedings of the IEEE*, 69(1), 14-47. | The 3x3 weighted-gradient slope method, the same one behind GDAL's and ArcGIS' slope. | `TOTAG` | `terrain.py::slope_degrees` |
| Spearman, C. (1904). "The proof and measurement of association between two things." *American Journal of Psychology*, 15(1), 72-101. | Rank correlation between a perturbed ranking and the baseline, in output 5. Computed as Pearson on midranks so ties are averaged. | -- | `sensitivity.py::spearman` |
| Hwang, C.-L., and Yoon, K. (1981). *Multiple Attribute Decision Making: Methods and Applications*. Lecture Notes in Economics and Mathematical Systems, vol. 186. Springer, Berlin. doi:10.1007/978-3-642-48318-9. Added 2026-10-10. | TOPSIS: rank by relative closeness to the ideal point and distance from the anti-ideal point, after vector normalisation and weighting. The method behind outputs 3 and 4. | -- | `mcdm.py::topsis`, `rank_flood_products` |
| Kendall, M. G. (1945). "The treatment of ties in ranking problems." *Biometrika*, 33(3), 239-251. | Tau-b, the tie-corrected rank correlation used beside Spearman in output 5. This paper is the tie correction specifically, which is the part that matters here: TOPSIS ties exactly at a crossing. | -- | `sensitivity.py::kendall_tau_b` |
| Triantaphyllou, E., and Sanchez, A. (1997). "A sensitivity analysis approach for some deterministic multi-criteria decision-making methods." *Decision Sciences*, 28(1), 151-194. | The idea output 5 is built on: the smallest change in a criterion weight that reverses a pair, and the "most critical criterion" that follows from it. **The construction here is not a port of theirs** -- they derive thresholds analytically for WSM/WPM/AHP; this bisects them numerically for TOPSIS, whose closeness is not linear in a weight. Cited as the lineage of the idea, not as the source of the arithmetic. | -- | `sensitivity.py` |

---

## 6. Cloud masking and scene usability

Added 2026-08-27. **Unverified: DOIs and page numbers have not been checked
against the originals.**

The 0.90 retention rule now has its citation (Martins Jr et al., below). Read
the sensor-mismatch caveat in its Context column before citing it in the
thesis: it is a Sentinel-1 result being used as a Sentinel-2 convention, and
that is a defensible choice only if stated, not if quietly inherited. The SSRN
reference is recorded as the user supplied it and has **not** been independently
verified.

| Source | Context | Vault note | Code |
|---|---|---|---|
| Main-Knorn, M., Pflug, B., Louis, J., Debaecker, V., Muller-Wilm, U., & Gascon, F. (2017). "Sen2Cor for Sentinel-2." *Proc. SPIE 10427, Image and Signal Processing for Remote Sensing XXIII*, 1042704. | The processor that generates SCL. Needed to state what the mask actually is rather than treating it as ground truth. | `[[Scene usability - SCL valid fraction]]` | -- (justifies the existing SCL probe in `sentinel.py`) |
| Baetens, L., Desjardins, C., & Hagolle, O. (2019). "Validation of Copernicus Sentinel-2 Cloud Masks Obtained from MAJA, Sen2Cor, and FMask Processors Using Reference Cloud Masks Generated with a Supervised Active Learning Procedure." *Remote Sensing*, 11(4), 433. | Quantifies how wrong SCL is as a cloud mask. The most likely source of a defensible number for `min_valid_fraction` (0.7 vs 0.9). | `[[Scene usability - SCL valid fraction]]` | -- (would justify the `min_valid_fraction` default) |
| Zhu, Z., & Woodcock, C. E. (2012). "Object-based cloud and cloud shadow detection in Landsat imagery." *Remote Sensing of Environment*, 118, 83-94. | Fmask. The alternative masking approach if SCL proves insufficient. | `TOTAG` | -- |
| Martins Jr, C., Capodici, F., Ciraolo, G., & De Marchis, M. "Shoreline compactness predicts reliability of Sentinel-1 water-area monitoring across reservoirs worldwide." SSRN preprint, doi:10.2139/ssrn.7226312. | **Source of the 0.90 retention rule.** Benchmarks Sentinel-1 water detectors -- single-polarisation VV Otsu threshold and dual-polarisation (VV+VH) SVM -- against JRC Global Surface Water, retaining observations at valid fraction >= 0.90 with a loose global 3-sigma clip for gross outliers. **Sensor mismatch, stated deliberately:** that is SAR water-area monitoring; `fetch_lulc_data` is optical Sentinel-2, where valid fraction is an SCL cloud mask, not a water-detection quality flag. The 0.90 is adopted as a quality-retention convention, not a transferred measurement. | `[[Scene usability - SCL valid fraction]]` | `sentinel.py`, `min_valid_fraction` default `0.9`. **The 3-sigma clip is deliberately NOT implemented** (user's decision, 2026-08-27). |

---

## 7. Sanitation, wastewater and health

The audit ledger for the Citadel sewage flow. **Nothing here is implemented**
-- no code fetches any of it. Added 2026-09-13.

**The full references live in the vault**, in
`Hand/Workflows/Chanakya flows/Human health risk - sources.md`. That
note is fetched at run time and shipped to the reader; this table is the
audit trail for a human checking the work, and is never retrieved. Keeping one
home for the reference text is what stops the two drifting.

**Vintage is a required field.** A source is reissued; a figure quoted without
its date silently ages into a false claim. The vintage column is the date of
the data or the publication, not the date it was read. Re-check for a newer
release before quoting any of these -- particularly the CPCB inventory (2021),
the INFORM release (annual) and the JMP update.

Verification: **read** means the source was opened and the figure taken from
it. **summary** means the figure comes from the publisher's own summary of the
source, not the source document. **listed** means the reference was confirmed
to exist and was not read.

| Source | Vintage | Context | Verification | Code |
|---|---|---|---|---|
| Ehalt Macedo, H., Lehner, B., Nicell, J., Grill, G., Li, J., Limtong, A., & Shakya, R. (2022). "Distribution and characteristics of wastewater treatment plants within the global river network." *Earth System Science Data*, 14(2), 559-577. doi:10.5194/essd-14-559-2022 | published 2022 | HydroWASTE. 58,502 plants, 58,278 georeferenced to the river network, ~4 billion people served. Population served, discharge, treatment level, outfall position, dilution factor. Outfall offset 6.5 +- 3.1 km, max 21.8 km -- the headline uncertainty of the hazard step. DF threshold of 10 is the European Medicines Agency's. CC-BY 4.0, <https://www.hydrosheds.org/products/hydrowaste> | read | input source 7; not wired |
| WHO, "Flooding and communicable diseases fact sheet", *Weekly Epidemiological Record* No. 3, 80th year, pp. 21-28. ISSN 0049-8114. <https://iris.who.int/handle/10665/232690> | 21 January 2005 | The risk framing the whole flow rests on: flood infection risk is **low unless there is significant population displacement and/or water sources are compromised**; 1 of 14 major floods 1970-1994 caused a major diarrhoeal outbreak. Tajikistan 1992 -- sewage plants flooded, river contaminated, no significant diarrhoeal increase. Leptospirosis as the only epidemic-prone directly water-transmitted infection. Chlorination: >99.99% inactivation of enteric bacteria and viruses, 0.2-0.5 mg/l residual at ~30 min. Power cuts disrupting water treatment and the cold chain. Corpses pose no epidemic risk. Tetanus/cholera/typhoid vaccination guidance. | read in full (PDF supplied by the user 2026-09-13) | -- |
| WHO, *Communicable diseases following natural disasters: risk assessment and priority interventions*, WHO/CDS/NTD/DCE/2006.4 | 2006 | Outbreak risk is lower than perceived and is driven by the displaced population's living conditions -- crowding, inadequate water and sanitation, poor health-service access. West Bengal 1998 (>16,000 cholera cases); Bangladesh 2004 (>17,000 diarrhoeal cases); Aceh/Calang (100% drinking from unprotected wells, 85% diarrhoea in two weeks); Muzaffarabad 2005 (>750 cases in a camp of 1,800, controlled by water and sanitation provision). **Hepatitis E case fatality up to 25% in pregnant women.** Diarrhoeal risk higher in developing than developed countries. Settlement planning and primary-care priority interventions. | read in full (PDF supplied by the user 2026-09-13) | -- |
| WHO, *Sanitation* fact sheet. <https://www.who.int/news-room/fact-sheets/detail/sanitation> | fact sheet dated 22 March 2024 | 1.4 M deaths/yr from inadequate WASH, 564,000 from unsafe sanitation; 1.5 bn without basic sanitation; 419 M open defecation; ~44% of household wastewater not safely treated; USD 5.50 returned per USD 1 invested (from a 2012 WHO study). | read | -- |
| WHO, *Guidelines on Sanitation and Health*. ISBN 978-92-4-151470-5. <https://www.who.int/publications/i/item/9789241514705> | 2018 | The reference for risk-based sanitation management and Sanitation Safety Planning; first-preference source for the recommendation step. | listed -- title, year and ISBN verified; recommendations not read | -- |
| WHO Regional Office for Europe, *WHO Guidelines for Indoor Air Quality: Dampness and Mould*. <https://www.who.int/publications/i/item/9789289041683> | 2009 | The mould hazard in wooden buildings and basements. Up to 75% greater risk of respiratory symptoms and asthma for occupants of damp or mouldy buildings; preventing persistent dampness is the primary measure. | executive summary read | -- |
| UNEP, *Wastewater - Turning Problem to Solution. A UNEP Rapid Response Assessment*. <https://www.unep.org/resources/report/wastewater-turning-problem-solution> | 2023 | About half of untreated wastewater reaches rivers, lakes and seas; 11% of treated wastewater is reused. Frames the circular-economy option in the recommendation step. | summary | -- |
| INFORM Risk Index, European Commission JRC / IASC. <https://drmkc.jrc.ec.europa.eu/inform-index/INFORM-Risk/Methodology> | methodology accessed 2026-09-13; index released annually -- **record the release year used** | The vulnerability step. Three dimensions -- Hazard & Exposure, Vulnerability, Lack of Coping Capacity -- scored 0-10 over ~80 indicators. Vulnerability = Socio-economic Vulnerability + Vulnerable Groups, geometrically aggregated. **National scale**, the step's standing caveat. | read | input source 8; not wired |
| INFORM Climate Change. <https://drmkc.jrc.ec.europa.eu/inform-index/INFORM-Climate-Change/Methodology> | accessed 2026-09-13 | Where a forward-looking vulnerability statement belongs. A separate product from INFORM Risk. | listed | input source 8; not wired |
| Central Pollution Control Board, India, *National Inventory of Sewage Treatment Plants*. <https://cpcb.nic.in/> | **March 2021** | Local-data example for an Indian AOI. 72,368 MLD urban sewage generation against 20,235 MLD utilised treatment capacity; 52,133 MLD untreated. **A newer inventory may exist -- check before quoting.** | summary -- the inventory PDF was not opened | -- |
| WHO, *Malaria* fact sheet. <https://www.who.int/news-room/fact-sheets/detail/malaria> | fact sheet dated 4 December 2025 | Module 10 hints (WHO 2025a): prevention by avoiding bites and with medicines; insecticide-treated nets and indoor residual spraying as the two core vector-control interventions; parasite-based diagnostic confirmation; early diagnosis and treatment. **Says nothing about floods.** | read 2026-11-02 (the page) | `Head/Output templates/Module 10 - Human health risks template` |
| WHO, *Dengue and severe dengue* fact sheet. <https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue> | fact sheet dated 21 August 2025 | Module 10 hints (WHO 2025b): *Aedes aegypti* the main vector; vector control; remove artificial habitats that hold water; cover, empty and clean domestic water storage weekly; routine vector surveillance. **Says nothing about floods**; the post-flood timing of malaria and dengue is NOT sourced -- the vector-borne section of WHO 2005 could not be retrieved 2026-11-02 (iris.who.int 403). | read 2026-11-02 (the page) | as above |
| WHO / UNICEF Joint Monitoring Programme. <https://washdata.org/> | updated periodically -- record the update used | Country sanitation and drinking-water service levels, for the pre-event baseline in the exposure step. | listed | input source 15; not wired |

---

## 8. Gazetteer and disaster register

How an area of interest and a time frame stop being a guess. Both are read by
`core/places.py` at the start of a session, once, and the result is kept in the
session record for every agent to work from. Added 2026-09-15.

| Source | Vintage | Used for | Verified |
|---|---|---|---|
| OpenStreetMap contributors. *Nominatim geocoding service*. <https://nominatim.org/>, via `geopy`. Data under ODbL. | continuously edited -- the retrieval date is recorded on the session record | Place name to bounding box (west, south, east, north, EPSG:4326), country and admin region. | live, `core/places.py::geocode_place` |
| Olbricht, R., and OpenStreetMap contributors. *Overpass API*. <https://overpass-api.de/>. Data under ODbL. Added 2026-10-10. | continuously edited -- no retrieval date is recorded yet | Counts of the mapped buildings, homes, hospitals and schools inside the AOI bounding box. Community survey data: a count is what is mapped, not what exists. | live, `core/workflows/place.py::fetch_place_context` (`places.OVERPASS_URLS`) |
| OpenStreetMap contributors. *Standard tile layer* (tile.openstreetmap.org), under the OSM Tile Usage Policy. <https://operations.osmfoundation.org/policies/tiles/>. Map data ODbL; tiles CC BY-SA. Added 2026-10-21. | rendered tiles, continuously updated; the retrieval date is on the payload | `where_am_i`: the map image of the AOI. Identifying User-Agent, at most 64 tiles a call, cached on disk, attribution "(c) OpenStreetMap contributors" drawn on the figure | live, `core/workflows/osm_context.py` (kelani-lower-2025, zoom 14, 2026-10-21) |
| Olbricht, R., and OpenStreetMap contributors. *Overpass API*: place nodes, admin_level 5 (district) and 4 (province) boundaries via `is_in` and `rel(bbox)`; feature counts. Added 2026-10-21. | continuously edited; retrieval date on the payload | `where_am_i`: first- and second-order admin units (levels read per country) and cities/towns; counts of buildings, hospitals, schools, commercial buildings, food or grocery shops, marketplaces and substations; seven water-related influence categories (one request), river crossings by exact line intersection | live, `core/workflows/osm_context.py`; admin levels checked 2026-10-21 -- Sri Lanka province 4, district 5; Thailand province 4, district 6, municipality 7, subdistrict 8 |
| European Commission Joint Research Centre & UN OCHA. *Global Disaster Alert and Coordination System (GDACS)*. <https://www.gdacs.org/>. | per event, with `fromdate` and `todate` on the record | Anchoring an approximate time frame to a recorded event: hazard type, start and end dates, alert level, GLIDE number where one was assigned. **Switched off for that use** (`places.GDACS_ENABLED`); since 2026-10-10 also listed as event links. | live, `core/places.py::find_event`; links via `places.gdacs_events` |
| European Commission. *Copernicus Emergency Management Service -- Rapid Mapping*, public activations list. <https://mapping.emergency.copernicus.eu/activations/>. Added 2026-10-10. | per activation; the public list starts in 2023 | Event links only: activations by country (or centroid in the AOI) and date. | live, `core/event_records.py::cems` |
| UNITAR / UNOSAT. Maps and datasets as published on the Humanitarian Data Exchange (HDX), organisation `unosat`. <https://data.humdata.org/organization/unosat>. Added 2026-10-10. | per dataset (`dataset_date`) | Event links only: UNOSAT datasets by country group and date. | live, `core/event_records.py::unosat` |
| German Aerospace Center (DLR). *Center for Satellite Based Crisis Information (ZKI)*, activations. <https://activations.zki.dlr.de/en/activations/>. Added 2026-10-10. | per activation | Event links only: activations by country and date, read from the list page (no API). | live, `core/event_records.py::zki` |
| Dartmouth Flood Observatory, University of Colorado. *Global Active Archive of Large Flood Events*. <https://floodobservatory.colorado.edu/Archives/index.html>. Added 2026-10-10. | archive index | Catalogue link only: no machine-readable event search found, and the server did not answer on 2026-10-10. | `core/event_records.py::dfo` |
| NASA Applied Sciences Disasters Program. *NASA Disasters*. <https://disasters.nasa.gov/>. Added 2026-10-10. | portal | Catalogue link only: no event search API found. | `core/event_records.py::nasa_disasters` |
| NASA LANCE. *NRT Global Flood Products* (MODIS/VIIRS). <https://www.earthdata.nasa.gov/data/instruments/viirs/near-real-time-data/nrt-global-flood-products>. Added 2026-10-10. Same product as S3/S11 in section 8b. | daily, by tile | Catalogue link only: there is no event index. | `core/event_records.py::nasa_flood_product` |

**Nominatim is a community gazetteer and is labelled as one** on every record it
produces. A bounding box is a reasonable thing to take from OpenStreetMap; a
claim about a place is not, and the source string is what stops the second from
being mistaken for the first.

**GDACS indexes by country, not by city.** The search uses the country the
gazetteer returned for the AOI, and the caller filters to the events that
overlap the window. An event with no GLIDE number has none -- GDACS assigns
them inconsistently, and an empty string is not a field to paper over.

**ReliefWeb was the first choice and is not usable.** Its v1 API returns 410,
and v2 rejects any `appname` that has not been approved through a request form
at <https://apidoc.reliefweb.int/>. If that approval is obtained it is a better
register for humanitarian events than GDACS -- named events rather than "Flood
in India" -- and belongs alongside it rather than instead of it.

---

## 8b. NRT flood products and the MCDM

The alternatives ranked by `rank_flood_products`, and the documentation behind
the thirty evidence cells in `core/workflows/_mcdm_data.py`. Source keys are
the workbook's own, so a cell citing `S1` in output 3 resolves here.

| Key | Source | Context | Code |
|---|---|---|---|
| S1 | Wagner et al. (2026). "The fully-automatic Sentinel-1 Global Flood Monitoring service: scientific challenges and future directions." *Remote Sensing of Environment* 333, 115108. https://www.sciencedirect.com/science/article/pii/S0034425725005127 | GFM: exclusion masks for dense vegetation and urban areas, wind-roughening behaviour, radar-shadow masking. | `_mcdm_data.EVIDENCE` |
| S2 | NASA Earthdata Forum: Flood Product FAQs (MODIS and VIIRS usage notes). https://forum.earthdata.nasa.gov/viewtopic.php?t=7707 | MODIS/VIIRS cloud, canopy and urban limitations. Several cells apply it **by sensor type** to DSWx-HLS, which is marked basis `S`, not `D`. | `_mcdm_data.EVIDENCE` |
| S3 | MODIS/VIIRS NRT Global Flood Products User Guide, Rev E. https://www.earthdata.nasa.gov/s3fs-public/2025-04/MCDWD_VCDWD_UserGuide_RevE_04.22.25.pdf | Terrain-shadow false positives and the HAND mask. | `_mcdm_data.EVIDENCE` |
| S4 | Earthdata / PO.DAAC: OPERA DSWx-HLS V1 collection page, known issues. https://www.earthdata.nasa.gov/data/catalog/pocloud-opera-l3-dswx-hls-v1-1.0 | HLS Fmask inheritance; the terrain-shadow layer. Whether it masks or only flags is **not confirmed** -- that cell is contested. | `_mcdm_data.EVIDENCE` |
| S5 | OPERA DSWx-S1 V1 collection description (Earth Engine catalogue). https://developers.google.com/earth-engine/datasets/catalog/OPERA_DSWX_L3_V1_S1 | Cloud independence; the inundated-vegetation class. | `_mcdm_data.EVIDENCE` |
| S6 | OPERA DSWx-S1 Product Specification Document v1.0.0, D-108761 Rev A. https://d2pn8kiwq2w21t.cloudfront.net/documents/OPERA_DSWx-S1_Product_Spec_v1.0.0_D-108761_RevA_2024-08-16_.pdf | Layover/shadow masking; the P1-P6 product values. | `_mcdm_data.STATIC_MATRIX` |
| S10 | NASA Earthdata: OPERA Near-Global DSWx Product Suite. https://www.earthdata.nasa.gov/data/projects/nsite/solutions/opera-near-global-dswx-product-suite | OPERA positioning SAR for mapping beneath vegetation; DSWx-HLS daylight dependence. | `_mcdm_data.EVIDENCE` |
| S11 | LANCE MODIS NRT Global Flood Product description page. https://floodmap.modaps.eosdis.nasa.gov/proddesc.php | MCDWD overpass times, canopy and terrain-shadow behaviour. | `_mcdm_data.EVIDENCE` |

### What is NOT sourced here, and must not be read as though it were

- **The parameters are author rules, every one.** The cloud curve's bounds and
  shape (`cloud_lower` 20, `cloud_upper` 100, `cloud_k` 3), the wind band
  (5-15 m/s) and the BUI threshold (0.2) have no citation. A reader who takes
  the cloud curve as empirical will over-trust what it produces.
- **Three of the thirty evidence cells have basis `R`** -- an author rule with
  no document behind it. All three are the "wind affects SAR only" rule
  applied to the optical products.
- **Five cells are contested**, meaning a second coder could reasonably score
  them differently. They are listed in output 3 with the weight their
  criterion carries.
- **P2 for the SAR products is unresolved**, pending the author's instruction
  on SAR revisit. The documented 6-12 day range is kept with the worst case
  used, which favours the optical products on that criterion.
- **`cloud_gate` was moved from 80 to 100 on 2026-09-22** so that high cloud
  penalises rather than excludes. See `docs/rank_flood_products.md` for the
  measurement behind that and for its tension with the vault's
  availability-is-a-precondition ruling.

---

## 8c. NRT product availability catalogues

What `data_availability` searches to say which NRT products observed an AOI on
which day. Catalogue metadata only -- no product file is downloaded. Added
2026-10-11; every endpoint searched without an account.

| Source | Vintage | Used for | Verified |
|---|---|---|---|
| EODC. *STAC API*, collection `GFM` (Copernicus EMS / GloFAS Global Flood Monitoring). <https://stac.eodc.eu/api/v1/collections/GFM> | items per Sentinel-1 frame; collection extent from 2015-01-01 | GFM acquisitions over the AOI per day | live, `availability.py` (Kelani: 5 acquisitions, 20 Nov - 5 Dec 2025) |
| NASA Earthdata. *CMR-STAC API*, provider `POCLOUD`: `OPERA_L3_DSWX-HLS_V1_1.0`, `OPERA_L3_DSWX-S1_V1_1.0`. <https://cmr.earthdata.nasa.gov/stac/POCLOUD> | per granule; DSWx-S1 from 2023-12-01 | OPERA DSWx acquisitions over the AOI per day | live, `availability.py` (Kelani: 8 HLS, 4 S1) |
| NASA Earthdata. *CMR-STAC API*, provider `LANCEMODIS`: `MCDWD_L3_NRT_6.1`, `VCDWD_L3_NRT_2`. <https://cmr.earthdata.nasa.gov/stac/LANCEMODIS> | **rolling, about one week** | MODIS / VIIRS NRT flood acquisitions, for days the archive still holds | live, `availability.py` |
| NASA Earthdata. *CMR search API*, `granules.json` sorted by start date. <https://cmr.earthdata.nasa.gov/search/> | live | The oldest LANCE granule still held -- the line between `none` and `not_checkable` | live, `availability.py::rolling_start` |
| NASA LANCE NRT archive. <https://nrt3.modaps.eosdis.nasa.gov/archive/allData/> | rolling | Evidence for the rolling window only: 2026/280 listed, 2025/328 a 404 (2026-10-11). Not queried at run time. | measured once, 2026-10-11 |
| Open-Meteo. *Forecast API*, `timezone=auto`. <https://api.open-meteo.com/v1/forecast> | live | The AOI's civil UTC offset, so days are local | live, `availability.py::utc_offset` |

The overpass cloud printed for MODIS and VIIRS is `fetch_weather_grid`'s
(section 1, ECMWF IFS via Open-Meteo, model named in the request). The
overpass times -- Terra ~10:30, Aqua and VIIRS ~13:30 local solar time -- are
the nominal equator-crossing times already used in section 8b's MCDM notes;
Terra and Aqua have drifted since 2022-23, so they are nominal, not measured.

---

## 8d. Validation reference and agreement metrics

What `accuracy_metrics` compares against, and where its metrics come from.
Added 2026-10-11. The metric definitions are checked in
`tests/test_metrics.py` against hand-computed matrices and scikit-learn; the
bibliographic details below were listed, not reopened.

| Source | Vintage | Used for | Verified |
|---|---|---|---|
| UNITAR-UNOSAT. *Flood extent, Sri Lanka, Cyclone Ditwah, 26 Nov - 2 Dec 2025*, FL20251128LKA, shapefile via the Humanitarian Data Exchange. **CC BY-SA -- credit UNOSAT.** | layer created 2025-12-04 (ArcGIS metadata) | The reference flood map. One dissolved layer, Sensor_ID Sentinel-2, "To be evaluated", "Not yet field validated"; no analysed extent | read from the delivered zip, 2026-10-11 |
| Copernicus Emergency Management Service, Rapid Mapping activation **EMSR851** (Cyclone Ditwah, Sri Lanka): **AOI02 *Kelani Ganga River* delineation product v1 (PlanetScope 3.9 m, 30 Nov 2025 05:39 UTC; semi-automatic extraction) -- the reference for kelani-basin-2025, the main experiment**; AOI09 *Gampola* grading product v1 (GeoEye 0.5 m, 4 Dec 2025 05:15 UTC) and AOI03 *Mahaveli Ganga River* delineation, monitoring 1, v1 (Legion 1.6 m, 3 Dec 2025 05:29 UTC). Shapefile zips as delivered to the user. Copyright notice by link only: <http://emergency.copernicus.eu/mapping/ems/cite-copernicus-ems-mapping-portal> (not yet read). Added 2026-11-18. | metadata dateStamp 2025-12-04 | **Reference flood maps for gampola-2025**: `observedEventA` features with notation *Flooded area* or *Flood trace* (user: trace counts as flood); domain = `areaOfInterestA` minus `notAnalysedA` and the mass-movement polygon. Photo-interpreted; not field validated. `modelledEventA` and `floodDepthA` are modelled (FABDEM) and never used as reference. Files and SHA-256: `experiments/gampola-2025/validation/reference/README.md` | read from the delivered zips, 2026-11-18 |
| Matthews, B. W. (1975). "Comparison of the predicted and observed secondary structure of T4 phage lysozyme." *Biochimica et Biophysica Acta*, 405(2), 442-451. | 1975 | MCC | listed |
| Jaccard, P. (1912). "The distribution of the flora in the alpine zone." *New Phytologist*, 11(2), 37-50. | 1912 | IoU | listed |
| Van Rijsbergen, C. J. (1979). *Information Retrieval*, 2nd ed. Butterworths. | 1979 | F1 (F-measure) | listed |
| Chicco, D., & Jurman, G. (2020). "The advantages of the Matthews correlation coefficient (MCC) over F1 score and accuracy in binary classification evaluation." *BMC Genomics*, 21, 6. | 2020 | Why MCC is reported beside F1 | listed |
| Congalton, R. G. (1991). "A review of assessing the accuracy of classifications of remotely sensed data." *Remote Sensing of Environment*, 37(1), 35-46. | 1991 | Confusion-matrix framework; overall accuracy's limits | listed |
| Bates, P. D., & De Roo, A. P. J. (2000). "A simple raster-based model for flood inundation simulation." *Journal of Hydrology*, 236(1-2), 54-77. | 2000 | IoU as the flood-extent fit measure | listed |
| EODC. *GFM ensemble flood extent and exclusion mask COGs* (`https://data.eodc.eu/collections/GFM_LAYERS/`), via the STAC API in section 8c. | per acquisition | The GFM product evaluated; encoding read from the files: 1/0/255, exclusion 1 = excluded | live, 2026-10-11 |
| NASA PO.DAAC, OPERA DSWx-HLS and DSWx-S1 data files | per granule | Downloads need an Earthdata login (403 without one, 2026-10-11). Since 2026-10-27 `get_flood_map` downloads the `B01_WTR` layers itself with `EARTHDATA_TOKEN` as a Bearer header (`core/workflows/opera.py`, BUG-029); checked over Edinburgh, 14-23 Aug 2026. Mannar: `scripts/fetch_opera_dswx.py` with the user's Earthdata token, 2026-10-19 -- 6 DSWx-HLS + 26 DSWx-S1 `B01_WTR` files, one superseded HLS reprocessing skipped; listed in `experiments/mannar-2025/validation/opera/manifest.json` | read on the native grid, `align.composite`, 2026-10-19 |
| NASA GIBS. *WMTS* `epsg4326/best`, layers `MODIS_Combined_Flood_{1,2,3}-Day` (from 2023-07-26) and `VIIRS_Combined_Flood_{1,2,3}-Day` (from 2025-06-24), tile matrix `250m`, with colormap `colormaps/v1.3/MODIS_Flood.xml`. <https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml> | per day | **MCDWD / VCDWD for past events**: the LANCE NRT archive keeps about a week and the user guide says there is no public archive of older products, so the classes are recovered from the rendered palette PNGs by `scripts/fetch_gibs_flood.py`. Values exact (palette -> colormap; unmapped colours reported); **geometry is GIBS's 0.0021973 deg grid, not the native 0.0020833 deg** | read, 2026-10-20: Kelani, Mannar, Puttalam, 21 Nov - 3 Dec 2025, no unmapped colour, no failed tile |
| Slayback, D. *LANCE MODIS/VIIRS Flood Product User Guide*, Revision E, 2025-04-22 (section 8b, key S3). | 2025-04-22 | Class values (0 no water, 1 surface water, 2 recurring flood -- "not yet populated", 3 flood, 255 insufficient data incl. HAND); composites (1-Day = product date, 2-Day adds the previous day, 3-Day the two before); observation thresholds (Table 2); "no publicly accessible archive of older products" | read, 2026-10-20 |
| JPL (2024). *OPERA Dynamic Surface Water Extent from Harmonized Landsat Sentinel-2 (DSWx-HLS) Product Specification*, v1.0.1, JPL D-107395 Rev B, 2024-07-10. <https://archive.podaac.earthdata.nasa.gov/podaac-ops-cumulus-docs/web-misc/opera/OPERA_DSWx-HLS_ProductSpec_v1.0.0_D-107395_RevB.pdf> | 2024-07-10 | DSWx-HLS WTR codes (Table 4-1, p. 10): flood = 1 open water + 2 partial surface water (>= 20% open water); no-data = 252, 253, 254, 255. User decision 2026-10-19; matches the spec's own BWTR union | read, Table 4-1, 2026-10-19 |
| JPL (2024). *OPERA DSWx-S1 Product Specification*, JPL D-108761, 2024-08-16 (section 8b, key S6). | 2024-08-16 | DSWx-S1 WTR codes (Table 4-1, p. 9): flood = 1 open water + 3 inundated vegetation; no-data = 250 HAND, 251 layover/shadow, 255 fill. User decision 2026-10-19; matches the spec's own BWTR union | read, Table 4-1, 2026-10-19 |

---

## 8e. The labelled sample

What the stratified labelled sample (`docs/sample_reference.md`) rests on.
Added 2026-10-19.

| Source | Vintage | Used for | Verified |
|---|---|---|---|
| Olofsson, P., Foody, G. M., Herold, M., Stehman, S. V., Woodcock, C. E., & Wulder, M. A. (2014). "Good practices for estimating area and assessing accuracy of land change." *Remote Sensing of Environment*, 148, 42-57. | 2014 | The design: probability sampling, stratification, sample size and a minimum per stratum, area-weighted estimates with confidence intervals | listed |
| Stehman, S. V. (2014). "Estimating area and map accuracy for stratified random sampling when the strata are different from the map classes." *International Journal of Remote Sensing*, 35(13), 4923-4939. | 2014 | The estimators: one stratification serving several products and the reference | listed |
| Element 84. *Earth Search STAC*, collection `sentinel-2-c1-l2a`. <https://earth-search.aws.element84.com/v1> | per scene | Sentinel-2 label chips (TCI true colour; B12/B8A/B04 false colour), and the pre-event scene `fetch_lulc_data` chose | live, `chips.py`, 2026-10-19 |
| Microsoft Planetary Computer. *STAC* collection `landsat-c2-l2`, anonymous SAS token. <https://planetarycomputer.microsoft.com/api/stac/v1> | per scene | Landsat 8/9 label chips. **Conflicts with section 9**, which drops Planetary Computer by decision; used because Earth Search's Landsat copy (`s3://usgs-landsat`) is requester-pays. **Pending the user's ruling** | live, `chips.py`, 2026-10-19 |
| *TODO(sample-buffer)*: a source for the 250 m S3 buffer | -- | Where flood-map omission concentrates relative to mapped flood edges | **missing**; the buffer is stated as an assumption |

---

## 8f. Context and impact tools

Added 2026-10-25, each checked live that day before any tool computed from it.
Every figure travels with its vintage in the payload (`sources[].vintage`).

| Source | Vintage | Used for | Code |
|---|---|---|---|
| World Bank. *World Development Indicators*: GDP (current US$), `NY.GDP.MKTP.CD`; Population, total, `SP.POP.TOTL`. API v2, <https://api.worldbank.org/v2/>, keyless, CC BY 4.0. | annual; latest non-null year returned (2025 for Sri Lanka, database updated 2026-10-08) | National GDP and population tiles in the Public Report and the Executive Summary (D-13). **National only**, and labelled so. | `context.py::fetch_world_bank_indicators` |
| European Commission JRC, DRMKC. *INFORM Risk Index* (Hazard & Exposure, Vulnerability, Lack of Coping Capacity, 0-10). API <https://drmkc.jrc.ec.europa.eu/Inform-Index/API/InformAPI/>, keyless. | twice a year ("INFORM Risk 2026" id 505, "Mid 2026" id 515 ...); the release used is the latest **published before** the date asked for, and is named in the payload | National vulnerability meters (D-13). The API serves no risk class, so none is reported. Sri Lanka, Mid 2026: INFORM 3.4, HA 3.2, VU 3.1, CC 4.0. | `context.py::fetch_inform_risk` |
| European Commission JRC, DRMKC. *INFORM Climate Change* 2022, "INFORM CC Brochure data.xlsx", <https://drmkc.jrc.ec.europa.eu/inform-index/INFORM-Climate-Change/Results-and-data>. Pessimistic = RCP 8.5 + SSP3; optimistic = RCP 4.5 + SSP1. | 2022 release; not served by the INFORM API | Climate outlook line (2050 and 2080, both scenarios). Keyed by **country name**, matched through the World Bank country name; a miss is NOT KNOWN, never a guess. Sri Lanka: baseline 3.4; 2050 P 3.6 / O 3.4; 2080 P 3.7 / O 3.4. Since 2026-11-11 the 2050 pessimistic value is the fourth meter of Modules 9-10 (the others in its note). | `context.py::fetch_inform_risk` |
| European Commission JRC, DRMKC. *INFORM Risk* back-calculated series: workflows named "INFORM Risk <release> - <year>", same API. | the latest series **published before** the date asked for, plus that release's own year; Kelani: INFORM Risk 2023 series, 2014-2023, published 31 Aug 2022 (read 2026-11-11) | Trend of Vulnerability (VU), Lack of Coping Capacity (CC) and Natural Hazard (HA.NAT, all natural hazards together) in Modules 9-10. One release's method for every year, so the years compare; the series ends before the release the meters use. Sri Lanka: VU 4.1 -> 2.7, CC 4.5 -> 4.0, HA.NAT 5.2 every year (2014 -> 2023). A year the API does not answer is a named gap. | `context.py::_trend` |
| World Bank. Country record, `api.worldbank.org/v2/country/<iso3>`, field `incomeLevel`. CC BY 4.0. | the **current** classification (revised each 1 July), read at fetch time -- not necessarily the one in force at the event date | The income-group phrase opening Vulnerability in Modules 9-10. Not served by INFORM; read from the record the INFORM tool already calls for the country name. Sri Lanka: Upper middle income (read 2026-11-11). | `context.py::country_record` |
| GDACS, Copernicus EMS Rapid Mapping, UNOSAT on HDX (section 8); World Bank *Documents & Reports* API, <https://search.worldbank.org/api/v3/wds>, keyless (added 2026-11-01). | per event, activation, product or document; World Bank documents are dated by the document | **Previous disasters, before the event window only** (D-15): every request is capped at the window start minus one day. Sources are national news and government reports or internationally recognised bodies (user decision 2026-11-01). World Bank: only post-disaster needs, damage and recovery documents naming the country, never project paperwork. **National news and government reports have no source**: ReliefWeb, the only route found, was removed on 2026-11-01 (it needs an approved appname), so they are reported `not_searched`. Every hit carries a short citation, publisher and year ("World Bank, 2017"). Copernicus EMS failed TLS verification from Python intermittently until 2026-11-01. | `disasters.py` |
| OpenStreetMap contributors via Overpass (section 8): `highway=*` ways with geometry, `bridge=*`, `amenity=townhall`, `office=government`, `building=government|industrial|factory|warehouse`, `landuse=industrial`, `amenity=school`; drinking water `man_made=water_works|water_tower|reservoir_covered` (not historic, ruined or disused), water pumping stations, wells with `drinking_water=yes`; wastewater `man_made=wastewater_plant` and sewage pumping stations; hospitals `amenity=hospital` / `healthcare=hospital` and general clinics only (`osm_context.hospital_or_general_clinic`; the same set in `where_am_i` and `assess_road_access`, BUG-037/038, 2026-10-27). | continuously edited; retrieval time on the payload | Road access, bridges and facility checks (D-12, D-37), and the extended `where_am_i` counts. Mapped features only. | `osm_context.py`, `impact.py` |
| Kramer, M., Terheiden, K., & Wieprecht, S. (2016). "Safety criteria for the trafficability of inundated roads in urban floodings." *International Journal of Disaster Risk Reduction*, 17, 77-84. doi:10.1016/j.ijdrr.2016.04.003. | 2016 | Road passability by depth (D-36): **0.3 m** the maximum safe depth for passenger cars, **0.6 m** for emergency vehicles. **The two figures are taken from the user's decision D-36; the article text was not read here** (the publisher page returned 403 on 2026-10-25; the citation was checked against Crossref). Applied only once a depth raster exists (D-37). | `impact.py::PASSABILITY` |

### Population (since 2026-10-30)

WorldPop is the one population source (user decision, 2026-10-30). The census
/ HDX COD-PS / World Bank chain and its centroid allocation were removed; their
entries are in section 9.

| Source | Vintage | Used for | Code |
|---|---|---|---|
| Bondarenko, M., Priyatikanto, R., ... Sorichetta, A. *WorldPop Global2 R2025A v1: constrained estimates of total number of people per grid square, 2015-2030*, 3 arc-seconds (~100 m), WGS84, people per pixel. WorldPop, University of Southampton (Global Demographic Data Project, Bill and Melinda Gates Foundation INV-045237). doi:10.5258/SOTON/WP00839. CC BY 4.0. Files: `https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/{YEAR}/{ISO3}/v1/100m/constrained/{iso3}_pop_{YEAR}_CN_100m_R2025A_v1.tif`, looked up through `https://hub.worldpop.org/rest/data/pop/G2_CN_POP_R25A_100m?iso3={ISO3}`. | one file per country and year, 2015-2030; R2025A is labelled an **alpha** release that "may change over the coming year". Sri Lanka 2025: 17.64 MB, float32, no-data -99999 (read from the file, 2026-10-30) | AOI population (`get_pop_data`): the sum of the pixels whose centre lies inside the bounding box, with the edge band as a range. Constrained: people only in mapped settlement. Method: random-forest dasymetric redistribution of census or projection totals (Stevens et al. 2015, PLoS ONE, doi:10.1371/journal.pone.0107042). No quantitative error is published for the count layers; WorldPop notes multi-year series are "likely to be less accurate" than single-year builds, and that constrained maps over-allocate to neighbouring settlements where buildings are missed. Checked live 2026-10-30: the hub JSON, the file and its header. The release statement PDF was not read. | `population.py::get_pop_data` |

**Not sourced, and labelled so.** The 30-degree slope threshold for the
slope-and-tree road notes (D-26) has no source yet; it is a config value marked
"source pending" in every payload that uses it. The 250 m hospital-to-road
distance is an assumption, marked `basis: assumed`.

## 8f-2. Modules 9 and 10 (map_hazard_exposure, people in the flood; 2026-11-02)

| Source | Vintage | Used for | Verification | Code |
|---|---|---|---|---|
| OpenStreetMap facilities via the Overpass API (hospitals and general clinics, drinking-water facilities, food shops and marketplaces), (c) OpenStreetMap contributors, ODbL | retrieved at run time; the date is stamped on the map and in the module | The exposure layers of the hazard-and-exposure map, and the counts in and out of the flood. Mapped features only. Food outlets grouped greedily into clusters no wider than 2 km (a display and counting rule, user decision 2026-11-02, not a service area). | the selectors are the `where_am_i` ones (BUG-038) | `core/workflows/hazard_map.py` |
| WorldPop Global2 R2025A (see section 1) crossed with the stored flood extent | the WorldPop year; the flood product's window | Population affected: each WorldPop pixel's people times the share of it the extent maps flooded (average resampling of the extent onto the WorldPop grid); people on land not observed are reported apart. | method, not a new source | `core/workflows/population.py::_in_extent` |

## 8g. Flood depth (calc_flood_depth, BUG-012)

Added 2026-10-26, each read that day before the tool computed anything from it.

| Source | Vintage | Used for | Code |
|---|---|---|---|
| Cohen, S., Peter, B. G., Haag, A., Munasinghe, D., Moragoda, N., Narayanan, A., & May, S. (2022). "Sensitivity of Remote Sensing Floodwater Depth Calculation to Boundary Filtering and Digital Elevation Model Selections." *Remote Sensing*, 14(21), 5313. doi:10.3390/rs14215313. CC BY 4.0. **Read in full** (the NOAA repository PDF, noaa_58788_DS1.pdf; the MDPI page returned 403). | 2022 | FwDET v2.1, section 2.1: boundary cells and their DEM elevation; the v2.0 coastal filter (boundary cells at or next to elevation <= 0 removed); the v2.1 slope filter (boundary cells with slope BELOW a threshold removed, in %); a 5 x 5 focal mean of the boundary elevation, iterated; each flooded cell takes its nearest boundary cell's elevation; depth = that minus the DEM. Defaults: "ten smoothing iterations and a slope threshold of 0.5 %", the stated optimum "for most DEMs" (abstract; Table 3). **GLO-30 was not among the DEMs tested**; the 30 m NASADEM optimum was [10, 2.5 %]. | `depth.py::fwdet` |
| csdms-contrib/fwdet, `FwDET2p1_GEE.txt` (the reference Earth Engine script), <https://github.com/csdms-contrib/fwdet>, GPL-2.0. Read 2026-10-26. | master | **Read to settle what the paper leaves open, and not followed where it departs from the paper**: its slope test keeps cells below the threshold, it has no coastal filter, and it convolves the depth at the end. The paper is what is cited. | (none: compared only) |
| European Space Agency & Airbus (2022). *Copernicus Digital Elevation Model Product Handbook*, version 5.0, 29.11.2022 (GEO.2018-1988-2), sections 1.2 and 2. doi:10.5270/ESA-c5d3d65. Read in full. | 2022 | The depth error (D-17): **relative vertical accuracy < 2 m (slope <= 20 %) and < 4 m (slope > 20 %), 90 % linear point-to-point error within 1 x 1 degree**; absolute vertical accuracy < 4 m (90 % linear error); global mean LE90ABS 1.92 m excluding Greenland and Antarctica (Table 12). "All accuracy statistics ... are calculated as an arithmetic mean on global level. Local deviations occur." The DEM "is a Digital Surface Model (DSM) which represents the surface of the Earth including buildings, infrastructure and vegetation" (section 1.1). The relative figure is perturbed; the absolute one cancels out of a depth. sigma = LE90 / 1.645 assumes normal errors (`basis: assumed`). | `depth.py::REL_LE90_M`, `error_realisations` |
| Kramer et al. (2016), section 8f. | 2016 | Vehicle classes on the depth (D-36): passable by car < 0.3 m, emergency vehicles only 0.3-0.6 m, impassable > 0.6 m. Still taken from D-36, not read here. | `depth.py::VEHICLE`, `impact.py::PASSABILITY` |

**Not sourced, so not computed: the person depth bands (D-25).** D-25 applies
the body-segment proportions of stature (Drillis & Contini 1966, as tabulated
in Winter) to the global average adult height (NCD-RisC 2016). Neither figure
was read on 2026-10-26: the main text of the NCD-RisC paper (eLife 2016,
5:e13410; the article page, read to the reference list) reports heights by
country and birth cohort -- **it gives no global mean** -- and the segment
table of Drillis, Contini & Bluestein (1964, Artificial Limbs 8(1)) is not in
the text of the O&P Library page. `report_config.PERSON_DEPTH_BANDS` stays empty
and every person band reads "Not known yet" until both are sourced.

---

## 9. Not used, and why

Kept so the decision is not re-litigated.

| Considered | Why not |
|---|---|
| DCS Census 2024 (GN table), HDX COD-PS, HDX COD-AB centroids for population | Replaced by WorldPop Global2 on 2026-10-30 (user decision): one gridded source for any country, instead of a Sri Lanka-only census adaptor with a centroid allocation and a World Bank national fallback that could not place people in an AOI. |
| Microsoft Planetary Computer | Dropped by decision. Earth Search serves the same Sentinel-2 COGs without signing, and the JRC and Copernicus buckets are reachable directly. |
| ESA WorldCover 10 m | A discrete label with no persistence information, and only 2020/2021 epochs. JRC occurrence carries the frequency that "permanent" actually means. |
| NDWI for permanent water | Circular. Deriving the baseline from the same scenes that measure the flood makes floodwater look permanent. |
| NASADEM / SRTM | 2000 vintage with voids in steep terrain -- the worst case for a mountainous AOI. |
| OPTRAM soil moisture | The real Sentinel-2-only method (Sadeghi et al. 2017), but it needs wet/dry edge calibration over a long time series and a wide region. Not derivable from a single cloudy lookback window. |
| Sentinel-1 SAR | The correct instrument for soil moisture and for flood extent under cloud. A substantially larger piece of work; not ruled out, just not built. Vault note: `[[SAR]]`. |
| Wikipedia for event confirmation | Easier than any register and inadmissible under verified-sources-only mode, which is a mode this project ships. A source that has to be withdrawn when the user tightens the setting is not a source. |
