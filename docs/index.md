# ptal-gtfs

**Public Transport Accessibility Levels (PTAL) for Indian cities, computed from
GTFS and OpenStreetMap.**

`ptal-gtfs` implements the Transport for London (TfL) PTAL methodology and extends
it for Indian conditions — heterogeneous walking environments, irregular headways,
and city-specific service patterns. Give it a GTFS feed and an OSM extract; get
back PTAL grids, maps, and reports for any city.

!!! warning "Status: planning / pre-alpha"
    The methodology and architecture are being finalised before implementation.
    The Python API shown in these docs is the **planned** interface and may change.

## Why this exists

PTAL is a widely used, easily communicated measure of how well a location is served
by public transport. TfL's method, however, assumes London-like conditions: formal
scheduled services, reliable headways, uniform reliability factors for a mode,
uniform walkability. Indian cities differ in ways that materially change the result
— a large share of trips run on **IPT** (shared autos, e-rickshaws, minibuses) that
rarely appears in GTFS; walking conditions vary sharply; headway irregularity makes
scheduled frequency a weaker proxy for waiting time; and different modes need
different access thresholds.

`ptal-gtfs` keeps the TfL framework — walk time + average waiting time → equivalent
doorstep frequency → accessibility index → PTAL band — but makes every parameter
explicit and configurable, ships a calibrated **India profile**, and adds an IPT
data layer for services without GTFS.

## Where to go next

- **[Install](install.md)** — set up the package (and how to install from source
  while it is pre-release).
- **[Quickstart](quickstart.md)** — the planned end-to-end workflow in a few lines.
- **Guide** — the [methodology](methodology.md), the [architecture](architecture.md),
  and the [data inputs](data.md) it expects.
- **[API reference](reference/index.md)** — generated from the source as it lands.

## License

[GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.en.html). Derivative works
must remain open source.
