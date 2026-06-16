# Workflow (the main API)

`PTALAnalysis` is the top-level entry point. Configure a run with `from_files`, call
`compute()`, then write outputs from the returned `PTALResult`. The OSM walk network is
downloaded automatically; everything about the *method* comes from the **profile**.

```python
from ptal_gtfs import PTALAnalysis

analysis = PTALAnalysis.from_files(
    gtfs={"dtc": "DTC_bus.zip", "dmrc": "DMRC_metro.zip"},
    service_date="2024-06-17",
    boundary="city_boundary.geojson",   # omit -> GTFS stops convex hull
    profile="india",                    # or "default" (TfL), or a path to a YAML
)

result = analysis.compute()
result.save("ptal")            # ptal.gpkg + ptal.csv + ptal_run.yaml (manifest)
result.plot_map("ptal.html")   # interactive choropleth (cells shaded by PTAL band)
result.bands                   # band distribution
result.grid                    # GeoDataFrame: poi_id, ai_<mode>, ai, ptal_band, geometry
```

`compute()` runs the whole pipeline — load feeds (peak window from the profile) → study
area → 100 m cell grid → OSM walk graph → per-mode SAP (`access_m` from the profile) →
`compute_ptal` — and records a **`run.yaml` manifest** (inputs, full resolved profile,
package version, summary) for reproducibility.

Pass **`verbose=True`** to `from_files(...)` or `compute()` to print each step with the
elapsed time — handy for seeing where a long run is spending its time (usually the OSM
download, or the grid size at whole-city scale):

```python
analysis.compute(verbose=True)
# [ptal    0.0s] loading GTFS feeds + peak frequencies ...
# [ptal   31.3s] downloading OSM walk network from Overpass (usually the slow part) ...
# [ptal   34.6s] done in 34.6s - 240 cells scored
```

!!! note "Scripts: exit cleanly"
    In a plain `.py` script the process can hang at the end, because pandana's native
    threads block interpreter shutdown on Windows. End the script with a hard exit:

    ```python
    import os
    os._exit(0)   # last line; scripts only — never in a notebook or web app
    ```

## API

::: ptal_gtfs.analysis
    options:
      show_root_heading: false
      members:
        - PTALAnalysis
        - PTALResult
