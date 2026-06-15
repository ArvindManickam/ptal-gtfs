# GTFS loading

The GTFS reader (`ptal_gtfs.io.gtfs`) is the first implemented part of the pipeline. It
loads one or more GTFS feeds, validates them, and reduces each to three tidy tables:

- **stops** — id, name, latitude, longitude (the candidate Service Access Points);
- **routes** — id, names, GTFS `route_type`, and the PTAL *mode* it maps to;
- **frequencies** — for every `(route, direction, stop)` served in the peak window on the
  chosen service date: number of departures, frequency (veh/h) and scheduled headway (min).

## One `FeedSource` per operator

Indian cities are usually served by several operators that each publish their **own GTFS
zip** (e.g. a city bus feed and a separate metro feed). You pass a *list* of feeds and the
reader merges them. Because independent feeds reuse the same ids (`route_id = "1"` can
exist in both), every id is **namespaced with the feed key** on load — `"bmtc:1"` versus
`"metro:1"` — so the merge never collides. The feed key is whatever short label you give
each source.

## Quick example

```python
from ptal_gtfs import FeedSource, load_feeds, inspect

# One FeedSource per operator. Separate zips are the common case; an unzipped
# directory of .txt files works too.
data = load_feeds(
    [
        FeedSource("bmtc", "data/bmtc_gtfs.zip"),
        FeedSource("metro", "data/namma_metro.zip"),
    ],
    service_date="2026-06-17",   # the weekday whose timetable to score
    peak_start="08:15",          # optional — TfL AM peak by default (methodology §1.4)
    peak_end="09:15",
)

print(inspect(data))             # validated summary: stops, routes, modes, headways
data.frequencies.head()          # the per-(route, direction, stop) peak frequency table
```

`inspect()` prints a summary like:

```text
GTFS summary for bmtc, metro
  service date : 2026-06-17  peak 08:15-09:15
  stops        : 4213
  routes       : 388  (bus: 372, metro: 16)
  served (route,dir,stop) pairs in peak : 51140
  headway min/median/max : 2.0 / 14.0 / 60.0 min
```

## Checking feed quality

Real feeds have quirks that otherwise surface as silent or confusing results — a service
date outside the calendar (zero frequencies), an empty `direction_id` (directions
merged), stops with missing coordinates. `check_feed()` reports them up front:

```python
from ptal_gtfs import FeedSource, check_feed

report = check_feed(FeedSource("dtc", "data/dtc_gtfs.zip"), service_date="2024-06-17")
print(report)
if not report.ok:        # True when there are no error-level issues
    raise SystemExit("feed cannot produce a PTAL run")
```

Example output:

```text
Feed 'dtc': 0 error(s), 1 warning(s)
  [WARNING] trips.txt has no direction_id; the two directions of each route are merged
  [INFO] service calendar spans 2024-01-01 to 2025-01-01
  [INFO] service_date 2024-06-17 (Monday) has 89393 active trips
  [INFO] 35238 stop_times after 24:00:00 (valid GTFS, after-midnight service)
```

Issues have three levels: **error** (blocks a usable run — missing required files, a date
outside the calendar, a date with no active trips), **warning** (degrades quality —
blank `direction_id`, bad coordinates, duplicate ids, dangling references) and **info**
(calendar span, active-trip count, after-midnight service, unmapped `route_type`).
`report.ok`, `report.errors` and `report.warnings` make it easy to gate a pipeline.

## Profiling a feed

`profile_feed()` / `profile_feeds()` compute descriptive statistics and distributions for
a service date — useful for understanding a city's network before computing PTAL (and for
choosing the peak window from the actual departures-by-hour curve rather than assuming
TfL's single hour):

```python
from ptal_gtfs import FeedSource, profile_feeds

prof = profile_feeds(
    [FeedSource("dtc", "data/dtc_gtfs.zip"), FeedSource("dmrc", "data/dmrc_gtfs.zip")],
    service_date="2024-06-17",
)
print(prof)                 # readable text report (incl. an hour-of-day histogram)
prof.by_mode               # routes / stops / trips / median headway per mode
prof.hourly_departures     # hour, mode, n_departures across the whole service day
prof.routes_per_stop       # interchange richness (distinct routes per stop)
```

The `FeedProfile` carries each result as a tidy frame/dict for your own plotting:
`totals`, `by_mode`, `hourly_departures`, `weekday_trips`, `headway_percentiles`,
`service_level_bands`, `routes_per_stop`, `stops_per_route`, and `extent` (bounding box +
centroid). Actual charts are deferred to the visualisation phase; the report uses a simple
text histogram for the hour-of-day curve.

## What you get back

`load_feeds(...)` returns a [`GtfsData`](#ptal_gtfs.io.gtfs.GtfsData) with three pandas
frames (all ids feed-namespaced, a `feed` column on each for provenance):

| Frame | Columns |
| --- | --- |
| `data.stops` | `feed, stop_id, stop_name, stop_lat, stop_lon` |
| `data.routes` | `feed, route_id, route_short_name, route_long_name, route_type, mode` |
| `data.frequencies` | `feed, route_id, direction_id, stop_id, mode, n_departures, frequency_vph, headway_min` |

Loading a single feed instead returns a [`Feed`](#ptal_gtfs.io.gtfs.Feed) with the same
three frames.

## Notes on behaviour

- **Service date** selects which trips run, resolved through `calendar.txt` and
  `calendar_dates.txt` exceptions. Pass a `datetime.date` or a `"YYYY-MM-DD"` string.
- **Peak window** is half-open `[start, end)`: a departure exactly at `peak_end` is *not*
  counted. The window is always a parameter — the TfL `08:15–09:15` is only the default.
- **`frequencies.txt`** is honoured: headway-based trips are expanded to their implied
  departures within the window.
- **Mode** comes from `route_type` via an overridable `mode_map` (the real per-city
  mapping becomes part of the config profile in Phase 3).
- **Input** may be a `.zip` archive or a directory of unzipped `.txt` files.

## API

::: ptal_gtfs.io.gtfs
    options:
      show_root_heading: false
      members:
        - FeedSource
        - load_feed
        - load_feeds
        - inspect
        - check_feed
        - profile_feed
        - profile_feeds
        - Feed
        - GtfsData
        - FeedSummary
        - FeedReport
        - FeedIssue
        - FeedProfile
        - GtfsValidationError
