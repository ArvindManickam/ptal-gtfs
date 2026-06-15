# Input data

`ptal-gtfs` needs three inputs (one optional):

| Input | Format | Required |
| --- | --- | --- |
| Public transport schedules | GTFS `.zip` (one or more feeds) | yes |
| Walking network | OSM extract (`.osm.pbf`) or automatic Overpass download | yes |
| IPT / informal services | CSV or GeoJSON layer | optional |
| Study boundary | GeoJSON/GPKG polygon, or named place, or GTFS extent | optional |

## GTFS

### Required files & fields

- `stops.txt` — `stop_id`, `stop_lat`, `stop_lon`
- `routes.txt` — `route_id`, `route_type`
- `trips.txt` — `trip_id`, `route_id`, `service_id` (`direction_id` strongly recommended)
- `stop_times.txt` — `trip_id`, `stop_id`, `departure_time`, `stop_sequence`
- `calendar.txt` and/or `calendar_dates.txt` — to resolve a service date
- `frequencies.txt` — honoured when present (common in Indian feeds)

### Known quality issues in Indian feeds (the validator must catch these)

- Missing `direction_id` (route de-duplication then falls back to route level)
- `route_type` misuse (e.g. metro coded as bus) — fixed via the profile's
  route-type mapping or per-route overrides
- Stale `calendar.txt` (feed validity window in the past) — warn, allow override date
- Duplicate/near-duplicate stops a few metres apart — optional stop clustering
- Times past 24:00:00 (legal GTFS; must be parsed correctly)
- `frequencies.txt`-only feeds with placeholder `stop_times`

### Where to get Indian GTFS

| City / source | Notes |
| --- | --- |
| Delhi — Open Transit Data (otd.delhi.gov.in) | DTC + cluster buses; static + realtime |
| Bengaluru — BMTC / BMRCL | bus + metro feeds |
| Kochi — KMRL open data | metro |
| Chennai, Hyderabad, Mumbai (BEST) | various portals / Transitland |
| Aggregators | transit.land, Mobility Database (mobilitydatabase.org) |

(Availability changes; verify links at use time. Multiple feeds per city are the
norm — the loader accepts a list.)

## OpenStreetMap

- Recommended: a **Geofabrik** regional extract (`india/<state>.osm.pbf`), clipped
  to the study area. Reproducible (snapshot the download date in the run manifest)
  and faster than Overpass for metro-scale areas.
- Convenience: automatic Overpass download for a named place (small areas only).
- The pedestrian network uses OSM ways where walking is permitted; the exact
  filter is part of the network config. Major-road classes used for crossing
  penalties (`trunk`, `primary`, …) are configurable.
- Caveat: footpath mapping completeness varies widely across Indian cities. When
  footways are sparsely mapped, the road network *is* the walking network — which
  is usually realistic in India anyway. Document the assumption per study.

## IPT layer

A minimal schema, deliberately easy to produce from field surveys:

**Stop-based services** (CSV or GeoJSON points):

```
service_id, name, mode, stop_lat, stop_lon, headway_min_peak
SA-01, Kothapet–Dilsukhnagar shared auto, ipt, 17.3712, 78.5512, 4
```

**Corridor-based services** (GeoJSON LineString + properties): hail-anywhere
services; the loader samples virtual stops along the line at a configurable
spacing (default 100 m).

```json
{ "type": "Feature",
  "geometry": { "type": "LineString", "coordinates": [...] },
  "properties": { "service_id": "ER-12", "mode": "ipt", "headway_min_peak": 3 } }
```

Headways may be observed (preferred) or assumed; the run manifest records which.

## Coordinate reference systems

All inputs are accepted in EPSG:4326. Internally, distances are computed in an
automatically selected projected CRS (UTM zone of the study area centroid),
overridable in the profile. Outputs default to EPSG:4326 with the metric CRS noted
in the manifest.
