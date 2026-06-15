# Quickstart

!!! warning "Planned API"
    `ptal-gtfs` is pre-alpha. The workflow below is the **intended** public API.
    It is documented here so the shape of the tool is clear; the implementation
    is in progress.

!!! tip "Available now"
    The GTFS loading layer is implemented and usable today — see
    [GTFS loading](reference/gtfs.md) to load feeds and build the peak frequency table.

## What you need

Three open inputs (see [Data inputs](data.md) for details and Indian sources):

| Input | Format | What it is |
| --- | --- | --- |
| GTFS feed | `.zip` | Routes, stops and timetables |
| OSM extract | `.pbf` | The pedestrian walking network |
| Study-area boundary | `.geojson` / `.gpkg` | The area to score |

Optional layers — informal transport (IPT), population, jobs — can be added for
richer inference.

## Score a city in a few lines

```python
from ptal_gtfs import PTALAnalysis

analysis = PTALAnalysis.from_files(
    gtfs="city_gtfs.zip",
    osm="city.osm.pbf",
    boundary="city_boundary.geojson",
    profile="india",       # or "default" to reproduce TfL exactly
)

result = analysis.compute()
result.to_geopackage("ptal.gpkg")   # PTAL grid for GIS
result.plot_map("ptal.html")        # interactive map
```

## What happens

`ptal-gtfs` keeps the TfL pipeline — **walk time + average waiting time → equivalent
doorstep frequency → accessibility index → PTAL band** — and runs it for every point
on a grid across your boundary. The chosen **profile** supplies every parameter
(walk speed, access thresholds, reliability factors, peak window, band edges), so
`default` reproduces the published TfL method and `india` applies the adapted values.

See the [Methodology](methodology.md) for the formulas and the
[API reference](reference/index.md) for the full set of options.
