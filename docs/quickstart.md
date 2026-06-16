# Quickstart

!!! note "Pre-alpha"
    `ptal-gtfs` is pre-alpha; parameter values in the `india` profile are placeholders
    pending the open methodology decisions. The workflow below works today.

## What you need

You only need **two** inputs — the OSM walking network is downloaded automatically:

| Input | Format | What it is |
| --- | --- | --- |
| GTFS feed(s) | `.zip` or unzipped dir | Routes, stops and timetables (one per operator) |
| Study-area boundary | `.geojson` / `.gpkg` / … | The area to score (optional — see below) |

If you omit the boundary, the study area is the **convex hull of the GTFS stops**.
Optional layers — informal transport (IPT), population, jobs — come later.

## Score a city in a few lines

### Without an OSM file (default — the network is downloaded)

You don't supply OSM; `compute()` downloads the walk network for your boundary:

```python
from ptal_gtfs import PTALAnalysis

analysis = PTALAnalysis.from_files(
    gtfs={"dtc": "DTC_bus.zip", "dmrc": "DMRC_metro.zip"},  # one entry per operator
    service_date="2024-06-17",                              # the weekday to score
    boundary="city_boundary.geojson",                       # omit -> GTFS stops hull
    profile="india",                                        # or "default" for TfL
)

result = analysis.compute()                 # OSM walk network downloaded here (Overpass)
result.save("ptal")                         # -> ptal.gpkg + ptal.csv + ptal_run.yaml
result.plot_map("ptal.html")                # interactive map
result.bands                                # band distribution
```

### With a saved OSM network (offline / reproducible)

Pass `osm=` a GraphML file you saved earlier, and `compute()` skips the download:

```python
analysis = PTALAnalysis.from_files(
    gtfs={"dtc": "DTC_bus.zip", "dmrc": "DMRC_metro.zip"},
    service_date="2024-06-17",
    boundary="city_boundary.geojson",
    profile="india",
    osm="city_walk.graphml",                # use this saved graph instead of Overpass
)

result = analysis.compute()                 # no network access
```

`osm` accepts `"overpass"` (the default) or a **GraphML** path — not `.osm.pbf`.

`result.save(...)` also writes **`ptal_run.yaml`** — a manifest recording the inputs,
the full resolved profile and the package version, so any run is reproducible.

## What happens

`ptal-gtfs` keeps the TfL pipeline — **walk time + average waiting time → equivalent
doorstep frequency → accessibility index → PTAL band** — and runs it for every point
on a grid across your boundary. The chosen **profile** supplies every parameter
(walk speed, access thresholds, reliability factors, peak window, band edges), so
`default` reproduces the published TfL method and `india` applies the adapted values.

See the [Methodology](methodology.md) for the formulas and the
[API reference](reference/index.md) for the full set of options.
