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
        - Feed
        - GtfsData
        - FeedSummary
        - GtfsValidationError
