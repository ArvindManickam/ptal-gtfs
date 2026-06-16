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

## API

::: ptal_gtfs.analysis
    options:
      show_root_heading: false
      members:
        - PTALAnalysis
        - PTALResult
