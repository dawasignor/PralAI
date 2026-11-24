"""Agreement of the NRT flood products with a reference flood map.

The steps of the validation plan (`validation_plan_unosat.md`), one module each:

    config.py     read and check config.yaml -- no hard-coded paths, no invented values
    metrics.py    confusion matrix and the agreement metrics
    reference.py  read the reference shapefile and rasterise it (step 1)
    domain.py     where reference and products are both meaningful (step 2)
    align.py      each product on its own native grid, composited (step 3)
    evaluate.py   reference aggregated to each product grid, compared (step 4)
    table.py      the one table, CSV and Markdown (step 5)

The registered workflow is `core/workflows/accuracy.py::accuracy_metrics`; Monty
runs it, and Entro interprets the table it produces
(`agents/entro/workflows/accuracy_interpretation.md`).

**Everything here is "agreement with the reference", never "accuracy".** The
reference is an operational map, not field truth (plan rule 3).
"""
