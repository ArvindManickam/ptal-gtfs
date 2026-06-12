# Architecture

## Design goals

1. **Faithful & auditable** — the pipeline mirrors the steps in
   [methodology.md](methodology.md) one-to-one, so results can be traced.
2. **Configurable** — all methodology parameters come from validated config
   profiles; code contains no magic numbers.
3. **Scalable** — metropolitan grids are 10⁵–10⁶ points × 10³–10⁴ stops; the hot
   path must be vectorised/native, never a per-point Python loop.
4. **Composable** — each stage is usable on its own from Python (load a feed,
   build a network, compute frequencies) so researchers can deviate from the
   standard pipeline.

## Package layout (src layout)

```
src/gtfs_ptal/
├── __init__.py          # public API: PTALAnalysis, PTALResult, load_profile
├── config/
│   ├── schema.py        # pydantic models: Profile, ModeConfig, GridConfig...
│   └── profiles/        # default.yaml (TfL), india.yaml
├── io/
│   ├── gtfs.py          # feed loading, validation, peak frequency table
│   ├── osm.py           # pedestrian network extraction & caching
│   └── ipt.py           # informal-transit layer (CSV/GeoJSON → virtual GTFS-like)
├── grid.py              # study area + POI grid generation
├── network.py           # walking-network routing (pandana wrapper)
├── core/
│   ├── sap.py           # POI→stop walk times, route de-duplication
│   ├── awt.py           # SWT / AWT / irregularity-based AWT
│   └── ptal.py          # EDF, AI, banding
├── outputs/
│   ├── export.py        # GeoPackage / GeoParquet / GeoTIFF / CSV
│   ├── maps.py          # folium HTML maps, PTAL colour scheme
│   └── report.py        # run summary + run.yaml manifest
└── cli.py               # typer app: compute, inspect, profile, map
```

## Data flow

```
GTFS feed(s) ─┐
              ├─► frequency table (route, dir, stop, mode, veh/hr)
IPT layer ────┘                                   │
                                                  ▼
OSM extract ──► walking network ──► POI×stop walk times ──► SAP table
                                                  │
boundary/place ──► POI grid ──────────────────────┤
                                                  ▼
profile (YAML) ──────────────► core: AWT → EDF → AI → band
                                                  │
                                                  ▼
                              PTALResult ──► gpkg / parquet / tiff / html / report
```

Intermediate artifacts (frequency table, walk-time matrix) are plain
pandas/GeoPandas objects, exposed on the result for inspection and testing.

## Key dependency choices

| Concern | Choice | Why |
| --- | --- | --- |
| GTFS parsing | `partridge` (or `gtfs-kit`) | lazy, service-date-aware loading of large feeds |
| OSM network | `osmnx` for extraction | standard, robust pedestrian network building |
| Shortest paths | `pandana` | contraction-hierarchy aggregate queries: nearest-N-POIs for 10⁵ origins in seconds — this is the scalability linchpin |
| Geometry | `geopandas`/`shapely 2` | vectorised geometry ops |
| Config | `pydantic` + YAML | validated, documented, user-extensible profiles |
| CLI | `typer` + `rich` | typed commands, good UX |
| Maps | `folium` | zero-server interactive HTML |

## Scalability strategy

- **Distance computation:** pandana's `nearest_pois` answers "k nearest stops
  within radius X for every grid point" natively; we never materialise a full
  POI×stop matrix. Per-mode radii keep candidate sets small.
- **Frequencies:** computed once per (route, dir, stop) with pandas groupbys —
  independent of grid size.
- **Memory:** SAP table is the only large intermediate (~POIs × reachable stops);
  processed with categorical dtypes and, if needed, in spatial chunks.
- **Caching:** the walking network (expensive to build) is cached on disk keyed by
  OSM extract hash + network config; reruns with different profiles skip it.
- **Parallelism:** grid chunks are embarrassingly parallel; a simple
  multiprocessing pool first, dask only if a real need is demonstrated.

## Configuration model

A *profile* fully determines a run:

```yaml
# profiles/india.yaml (sketch)
name: india
extends: default
peak_window: { start: "08:00", end: "10:00" }
grid: { spacing_m: 100 }
walk: { speed_m_per_min: 72, crossing_penalty_min: 0.5 }
modes:
  bus:   { route_types: [3], max_access_min: 8,  reliability_min: 3.0 }
  metro: { route_types: [1], max_access_min: 12, reliability_min: 0.75 }
  ipt:   { source: ipt_layer, max_access_min: 5, reliability_min: 2.0 }
bands: tfl   # keep TfL thresholds for comparability
```

Profiles support `extends`, so a city profile only overrides what differs.
Values shown are placeholders until decisions D1–D6 (PLAN.md) are resolved.

## Testing strategy

- **Golden tests:** `default` profile reproduces the TfL worked example exactly.
- **Fixture city:** a tiny synthetic GTFS + OSM clip with hand-computable PTAL.
- **Property tests:** band edges, monotonicity (more frequency ⇒ AI never falls).
- **Regression benchmark:** timing on the fixture scaled up, to catch hot-path
  regressions.
