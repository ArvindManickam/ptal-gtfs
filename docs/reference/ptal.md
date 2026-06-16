# PTAL computation & profiles

The core computes the Accessibility Index (AI) and PTAL band for every grid point from two
inputs already produced upstream: the **access table** (walk distances per grid point and
stop, from [`nearest_stops`](network.md)) and the **peak frequencies** (per route,
direction and stop, from [GTFS loading](gtfs.md)). It follows the TfL formulas
(methodology §1.3–1.7); a **config profile** supplies the parameters.

## End-to-end

```python
from ptal_gtfs import load_feeds, FeedSource, compute_ptal
from ptal_gtfs.grid import load_boundary, make_grid
from ptal_gtfs.io.osm import build_walk_graph
from ptal_gtfs.network import build_walk_network, nearest_stops

gtfs = load_feeds([FeedSource("dtc", "data/dtc_gtfs.zip")], "2024-06-17")
area = load_boundary("data/delhi_boundary.geojson")
grid = make_grid(area, spacing_m=100)
walk = build_walk_network(build_walk_graph(area), grid, gtfs.stops, k_centroid=3, k_stop=3)

stop_modes = gtfs.frequencies[["stop_id", "mode"]].drop_duplicates()
access = nearest_stops(walk, {"bus": 500, "metro": 2000}, max_n=50, stop_modes=stop_modes)

ptal = compute_ptal(access, gtfs.frequencies, profile="india")
# -> poi_id, ai_<mode> per mode, ai (total), ptal_band
```

`compute_ptal(access, frequencies, *, profile=None, ...)`:

- joins access to frequencies on `stop_id`;
- **WT** = walk / walk_speed, **SWT** = 30 / f, **AWT** = SWT + K, **TAT** = WT + AWT,
  **EDF** = 30 / TAT;
- de-duplicates each route to its best SAP (largest EDF) per grid point (§1.3);
- **AI per mode** = best EDF × 1.0 + every other × 0.5; **total AI** = Σ over modes (§1.6);
- maps total AI onto the **band** table (§1.7).

## Profiles select the method

A profile (`ptal_gtfs.config`) supplies the walk speed, the reliability model and the band
table. `compute_ptal(..., profile=...)` takes a `Profile`, a shipped name, or a YAML path;
`None` uses the TfL `default`.

| Profile | `reliability.kind` | AWT |
| --- | --- | --- |
| `default` (TfL) | `static` | `SWT + K` (fixed per-mode K: bus 2.0, rail/metro/tram 0.75) |
| `india` | `deviation` | `SWT + headway × factor` (per-mode factor: bus 0.2, rail/metro/tram 0.05) |

```python
from ptal_gtfs import load_profile
p = load_profile("india")          # or a path; or build a Profile directly
p.reliability.kind                 # "deviation"
```

The `default` profile reproduces TfL exactly and is the unit-tested baseline; `india` is the
adaptation. Parameter values in `india` are placeholders pending the open methodology
decisions (D1–D6). To make a city profile, copy a shipped YAML and edit it.

## API — PTAL core

::: ptal_gtfs.ptal
    options:
      show_root_heading: false
      members:
        - compute_ptal
        - walk_time
        - scheduled_waiting_time
        - average_waiting_time
        - equivalent_doorstep_frequency
        - accessibility_index
        - ptal_band

## API — profiles

::: ptal_gtfs.config
    options:
      show_root_heading: false
      members:
        - load_profile
        - Profile
        - Reliability
        - Bands
