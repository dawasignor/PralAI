"""Checking a brief's internal arithmetic before it becomes a thesis figure.

The schema asks for `fraction` and `area_km2` in several places. They are the
same quantity twice, and **nothing in the schema requires them to agree** -- so
a pair that contradicts itself is still valid against it.

**Two denominators live in one document, and confusing them manufactures
failures.** This checker has now done that twice, in two different ways, and
both fixes are below.

    surface_composition   fractions are OF THE OBSERVED AREA
                          (the schema's own field is `fraction_of_observed`;
                          cloud and shadow are excluded)
    hydrography           fractions are of the FULL AOI -- JRC surface water
                          carries no cloud mask
    terrain_setting       fractions are of the FULL AOI -- nor does the DEM

**First failure: the wrong denominator everywhere.** The original version used
the AOI area for everything and reported 18 disagreements in a brief that had
one. Seventeen of them shared the ratio 0.848, which is exactly
`observed_area / aoi_area`. Seventeen "errors" sharing one constant is never
seventeen slips.

**Second failure: the right denominator, chosen by the wrong thing.** The fix
picked the denominator from the JSON path, so anything under
`surface_composition` was measured against the observed area. But a proxy's
denominator follows its SOURCE, not its position: a JRC permanent-water figure
quoted inside `surface_composition.thematic_signals` as a cross-check is still
AOI-relative, because JRC still has no cloud mask. The checker called that an
error at ratio 1.363 -- which is `aoi_area / observed_area`, the same tell as
before, one field instead of seventeen.

So position is now only a FIRST GUESS. A pair that fails against it is tried
against the other denominator, and if it agrees there it is reported as a
denominator note rather than an error. A pair that agrees with neither is the
only thing called a disagreement.

**Third failure: one observed area assumed, where the document has several.**
NMDI is masked wherever SWIR16 does not exceed SWIR22, so its valid area is
smaller than every other index's -- 949.46 km2 against 979.44 on the Kelani
AOI. The brief multiplied NMDI fractions by NMDI's own observed area, which is
correct, and the checker called it two disagreements at ratio 0.969.

That is three false accusations with one shape: **the checker knew fewer
denominators than the document legitimately used.** So it no longer holds a
fixed pair. `check_areas` takes every denominator that is real for this
document -- the land area, the scene-wide observed area, and any per-index
observed area the caller supplies -- and a pair is a disagreement only when it
matches NONE of them. What it matched is reported, so a reader can see which
area a figure is really a share of.

The deeper fix is not here: a figure should state its own denominator, the way
`hydrography.independent_water_estimates` already carries an `extent` field.
Until every section does, this checker is inferring something the document
should simply say.

**Fourth correction: a fraction carries only the precision it was written
with.** `0.0026` has two significant figures, so its half-ulp is +-5e-5 -- a
relative uncertainty of 1.9%, wider than the flat 1% tolerance. Two perfectly
correct pairs were failing on the rounding of their own stated fraction. The
tolerance is now the looser of 1% and the fraction's own precision.

**It reports; it does not repair.** A figure quietly corrected by a tool is
undetectable, and the correction would itself be unsourced. What comes back is
a list a reader can act on.
"""

from __future__ import annotations

from typing import Any

# How far a pair may disagree before it is worth reporting, as a floor. The
# real tolerance is the looser of this and the stated fraction's own precision,
# because a fraction written to two significant figures cannot imply an area to
# better than its own half-ulp.
TOLERANCE = 0.01

# Which denominator a section's fractions are taken against -- a FIRST GUESS
# from the JSON path, not a verdict. Anything unlisted is guessed at the
# observed area, because that is where the composition pairs mostly live. A
# pair that fails its guess is tried against the other one before anything is
# called an error.
AOI_RELATIVE = ("$.hydrography", "$.terrain_setting")


def _walk(node: Any, path: str = "$"):
    """Every dict in the document, with the path that reached it."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def _half_ulp(value: float) -> float:
    """Half the last decimal place the value was actually written to.

    `0.0026` was written to four decimals, so anything in [0.00255, 0.00265)
    would have been written the same way, and an area implied from it inherits
    that whole band. Treating the literal as exact turns the author's rounding
    into the checker's error.
    """
    text = repr(float(value))
    if "e" in text or "E" in text:          # scientific notation: assume exact
        return abs(value) * 1e-9
    _, _, decimals = text.partition(".")
    return 0.5 * (10.0 ** -len(decimals))


def _tolerance(fraction: float) -> float:
    """Relative tolerance for a pair, given how precisely the fraction is written."""
    if not fraction:
        return TOLERANCE
    return max(TOLERANCE, _half_ulp(fraction) / abs(fraction))


def _agrees(fraction: float, area: float, base: float, tol: float) -> bool:
    implied = fraction * base
    if implied <= 0 and area <= 0:
        return True
    denominator = max(abs(implied), abs(area), 1e-9)
    return abs(implied - area) / denominator <= tol


def check_areas(brief: dict[str, Any],
                extra_denominators: dict[str, float] | None = None
                ) -> list[dict[str, Any]]:
    """Every fraction/area pair that agrees with none of the real denominators.

    `extra_denominators` names areas this document legitimately uses that are
    not in it -- per-index observed areas, above all. Pass the ones the AGENT
    had: a checker working from fewer denominators than the author will
    manufacture disagreements, which is how this one produced 21 false
    findings across three revisions.

    Each entry carries a `kind`:

    * `disagreement` -- the pair matches no denominator at all. The only kind
      that means the brief is wrong.
    * `denominator` -- the pair is exact against a denominator other than the
      one its position implied, named in `agrees_against`. Usually a figure
      sourced from a layer with no cloud mask, or an index with a mask of its
      own. Worth a reader's eye; not an arithmetic error.
    """
    aoi_area = (brief.get("aoi") or {}).get("area_km2")
    if not isinstance(aoi_area, (int, float)) or aoi_area <= 0:
        return [{"path": "$.aoi.area_km2", "kind": "disagreement",
                 "problem": "no usable AOI area, so no pair can be checked"}]

    observed = (brief.get("observation_basis") or {}).get("observed_area_km2")
    if not isinstance(observed, (int, float)) or observed <= 0:
        observed = None

    # Every denominator that is real for this document, by name.
    others: dict[str, float] = {}
    for name, value in (extra_denominators or {}).items():
        if isinstance(value, (int, float)) and value > 0:
            others[name] = float(value)

    out: list[dict[str, Any]] = []
    for path, node in _walk(brief):
        fraction = node.get("fraction")
        if fraction is None:
            fraction = node.get("fraction_of_observed")
        area = node.get("area_km2")
        if not isinstance(fraction, (int, float)):
            continue
        if not isinstance(area, (int, float)):
            continue

        aoi_first = path.startswith(AOI_RELATIVE) or observed is None
        if aoi_first:
            guess, guess_name = aoi_area, "aoi.area_km2"
            other, other_name = observed, "observation_basis.observed_area_km2"
        else:
            guess, guess_name = observed, "observation_basis.observed_area_km2"
            other, other_name = aoi_area, "aoi.area_km2"

        tol = _tolerance(fraction)
        if _agrees(fraction, area, guess, tol):
            continue

        label = (node.get("label") or node.get("proxy") or node.get("theme")
                 or "")
        entry = {
            "path": path,
            "fraction": fraction,
            "area_km2_stated": area,
            "area_km2_implied": round(fraction * guess, 3),
            "ratio": round(area / (fraction * guess), 3) if fraction * guess else None,
            "against": guess_name,
            "tolerance": round(tol, 5),
            "label": label,
        }

        # Every other denominator this document really uses, nearest first, so
        # a near miss is not reported as agreement with something distant.
        candidates = dict(others)
        if other is not None:
            candidates[other_name] = other
        matched = next(
            (nm for nm, value in sorted(
                candidates.items(),
                key=lambda kv: abs(kv[1] * fraction - area))
             if _agrees(fraction, area, value, tol)), None)

        if matched is not None:
            entry["kind"] = "denominator"
            entry["agrees_against"] = matched
            entry["area_km2_implied_alt"] = round(fraction * candidates[matched], 3)
        else:
            entry["kind"] = "disagreement"
            entry["denominators_tried"] = sorted(
                [guess_name] + list(candidates))
            # The area the author must have divided by. Naming it turns "these
            # do not agree" into something a reader can go and look for.
            entry["denominator_implied"] = round(area / fraction, 2) if fraction else None
        out.append(entry)
    return out


def summarise(problems: list[dict[str, Any]]) -> str:
    """The findings as prose for the output document.

    Denominator notes are reported separately from disagreements and never
    counted as errors. Conflating the two is how this checker twice told a
    reader that a correct brief was broken.
    """
    errors = [p for p in problems if p.get("kind") != "denominator"]
    notes = [p for p in problems if p.get("kind") == "denominator"]

    lines: list[str] = []
    if not errors:
        lines.append(
            "Every `fraction` / `area_km2` pair agrees with one of the two "
            "denominators to within the greater of 1% and the fraction's own "
            "stated precision -- composition against the observed area, "
            "hydrography and terrain against the full AOI.")
    else:
        # A shared ratio across most rows means the checker picked the wrong
        # denominator, not that the brief is broken. Say so rather than handing
        # over a list that looks like a catastrophe.
        ratios = [p["ratio"] for p in errors if p.get("ratio")]
        hint = ""
        if len(ratios) > 3 and (max(ratios) - min(ratios)) < 0.02:
            hint = (f"\n\n**Note:** {len(ratios)} of these share the ratio "
                    f"{ratios[0]:.3f}. A constant ratio across many fields is a "
                    "denominator mismatch, not many separate errors -- check "
                    "which area the fractions are taken against before treating "
                    "these as defects.\n")
        lines.append(
            f"**{len(errors)} fraction/area pair(s) agree with neither the "
            "observed area nor the full AOI.** Reported, not corrected -- a "
            "figure quietly fixed by a tool is undetectable, and the fix would "
            "itself be unsourced." + hint)
        lines.append("")
        lines.append("| Field | Label | fraction | stated km2 | implied km2 | "
                     "ratio | against |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in errors:
            if "problem" in p:
                lines.append(f"| `{p['path']}` | -- | -- | -- | -- | -- | "
                             f"{p['problem']} |")
                continue
            lines.append(
                f"| `{p['path']}` | {p.get('label') or '--'} | {p['fraction']} "
                f"| {p['area_km2_stated']} | {p['area_km2_implied']} | "
                f"{p.get('ratio')} | `{p.get('against')}` |")

    if notes:
        lines.append("")
        lines.append(
            f"**{len(notes)} pair(s) are exact, but against the other "
            "denominator** from the one their position in the document implied. "
            "This is normal where a figure sourced from a layer with no cloud "
            "mask -- JRC surface water, the DEM -- is quoted inside a "
            "composition section as a cross-check: it stays a share of the "
            "full AOI wherever it is written. Listed so a reader can confirm "
            "that is what happened, not as errors.")
        lines.append("")
        lines.append("| Field | Label | fraction | stated km2 | agrees against |")
        lines.append("|---|---|---|---|---|")
        for p in notes:
            lines.append(
                f"| `{p['path']}` | {p.get('label') or '--'} | {p['fraction']} "
                f"| {p['area_km2_stated']} | `{p.get('agrees_against')}` |")

    lines.append("")
    return "\n".join(lines)
