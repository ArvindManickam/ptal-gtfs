# gtfs-ptal

**Public Transport Accessibility Levels (PTAL) for Indian cities, computed from GTFS and OpenStreetMap.**

`gtfs-ptal` is a Python package that implements the Transport for London (TfL) PTAL
methodology and extends it for Indian conditions — heterogeneous walking environments, irregular headways, and city-specific service patterns. It takes a GTFS feed and an OSM extract as input and produces PTAL grids, maps, and reports for any city.

> **Status: planning / pre-alpha.** The methodology and architecture are being
> finalised before implementation.

## Why this exists

PTAL is a widely used, easily communicated measure of how well a location is served
by public transport. TfL's method, however, assumes London-like conditions: formal
scheduled services, reliable headways, uniform reliability factors for a mode, uniform walkability. Indian cities differ in ways that materially change the result:

- A large share of trips are served by **IPT** (shared autos, e-rickshaws, minibuses) that rarely appears in GTFS.
- **Walking conditions** vary sharply — footpath availability, road-crossing barriers, and walking speeds are not uniform.
- **Headway irregularity** makes scheduled frequency a weaker proxy for waiting time.
- Mode mixes (metro, suburban rail, BRT, city bus, IPT) need different access
  thresholds and reliability assumptions.

`gtfs-ptal` keeps the TfL framework (walk time + average waiting time → equivalent
doorstep frequency → accessibility index → PTAL band) but makes every parameter
explicit and configurable, ships a calibrated **India profile**, and adds an IPT data layer for services without GTFS.

## Planned features

- **GTFS ingestion** — load, validate, and compute peak-period frequencies from any
  GTFS feed (single or multiple feeds per city).
- **OSM walking network** — true network walking distances (not crow-fly buffers)
  via OSMnx/pandana, with optional crossing/barrier penalties.
- **IPT layer** — supply informal services as simple CSV/GeoJSON (stops/corridors +
  observed headways) and have them treated as a first-class mode.
- **Configurable methodology** — every TfL parameter (walk speed, max access time,
  reliability factor, peak window, band thresholds) lives in a config profile;
  `default` reproduces TfL, `india` applies the adapted values.
- **Scalable computation** — vectorised shortest-path and frequency computations
  designed to handle metropolitan-scale grids (lakhs of grid points).
- **Outputs** — PTAL grid as GeoPackage/GeoParquet/GeoTIFF, interactive HTML maps,
  ward/zone aggregations, and summary reports.
- **Simple Python API** — usable from a script or notebook in a few lines. (A CLI
  wrapper may be added in a later phase, once the library is stable.)

```python
# Python API (planned)
from gtfs_ptal import PTALAnalysis

analysis = PTALAnalysis.from_files(
    gtfs="city_gtfs.zip",
    osm="city.osm.pbf",
    profile="india",
)
result = analysis.compute()
result.to_geopackage("ptal.gpkg")
result.plot_map("ptal.html")
```

## Documentation

| Document | Contents |
| --- | --- |
| [docs/methodology.md](docs/methodology.md) | The TfL PTAL method, formulas, and the Indian adaptations |
| [docs/data.md](docs/data.md) | Input data requirements — GTFS, OSM, IPT — and Indian data sources |

## Installation

Not yet published. Once released:

```bash
pip install gtfs-ptal
```

## Citing

If you use `gtfs-ptal` in academic work, a `CITATION.cff` will be provided with the
first release. The underlying methodology is:

> Transport for London (2015). *Assessing transport connectivity in London*.

## License

[GPL-3.0](LICENSE). Derivative works must remain open source.
