"""GTFS reader for PTAL.

This module loads one or more GTFS feeds, validates them, and reduces each to the
three things the PTAL engine needs:

* **stops**       — id, name, latitude, longitude (the candidate Service Access Points);
* **routes**      — id, names, GTFS ``route_type`` and the PTAL *mode* it maps to;
* **frequencies** — for every (route, direction, stop) served in the peak window on the
                    chosen service date: number of departures, frequency (veh/h) and
                    scheduled headway (min).

Indian cities are typically served by several operators that each publish their **own
GTFS zip** (e.g. a city bus feed and a separate metro feed). The reader therefore takes
a *list* of feeds and merges them. Because independent feeds reuse the same ids
(``route_id = "1"`` can exist in both), every id is **namespaced with the feed key**
(``"bmtc:1"``) on load, so the merge is collision-free.

Methodology references (``docs/methodology.md``):
  §1.4  frequency is measured in the AM peak window (TfL default 08:15–09:15);
  §2    frequency = departures within the peak window on the selected service date,
        resolved via ``calendar``/``calendar_dates``, with ``frequencies.txt`` honoured;
  §2    ``route_type`` → PTAL mode mapping is part of the config profile (Phase 3); the
        default map below is a stand-in until the profile system exists.

Performance notes: the loader runs once per feed, not per grid point, so it is not the
PTAL hot path. Even so it is written to scale — only the needed columns are read, the
huge ``stop_times`` table is filtered to active trips *before* times are parsed, and all
counting is vectorised (pandas/numpy ``groupby``). The only Python-level loops are over
*feeds* and over ``frequencies.txt`` rows, both of which are tiny.
"""

from __future__ import annotations

import datetime as _dt
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Configuration constants
# --------------------------------------------------------------------------------------

# Default GTFS ``route_type`` → PTAL mode mapping. This is the standard GTFS spec, used
# as a stand-in until the config-profile system (Phase 3) owns the mapping. Indian feeds
# use ``route_type`` inconsistently, which is exactly why the map is overridable via the
# ``mode_map`` argument rather than hard-coded in the counting logic.
DEFAULT_ROUTE_TYPE_MAP: dict[int, str] = {
    0: "tram",
    1: "metro",
    2: "rail",
    3: "bus",
    4: "ferry",
    5: "cable_tram",
    6: "aerial_lift",
    7: "funicular",
    11: "trolleybus",
    12: "monorail",
}

# TfL peak window (methodology §1.4). Used as the *default* argument value only; the
# window is always a parameter so any profile/city can override it.
DEFAULT_PEAK_START = "08:15"
DEFAULT_PEAK_END = "09:15"

# Required files and the fields PTAL cannot do without. ``calendar``/``calendar_dates``
# are handled separately (at least one of the two must be present).
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "stops.txt": ("stop_id", "stop_lat", "stop_lon"),
    "routes.txt": ("route_id", "route_type"),
    "trips.txt": ("trip_id", "route_id", "service_id"),
    "stop_times.txt": ("trip_id", "stop_id"),  # a departure or arrival time also required
}


class GtfsValidationError(ValueError):
    """Raised when a feed is missing files or fields PTAL needs."""


# --------------------------------------------------------------------------------------
# Public data containers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedSource:
    """One GTFS feed to load.

    Parameters
    ----------
    key:
        Short identifier for the operator/feed (e.g. ``"bmtc"``, ``"metro"``). Used to
        namespace every id in the feed so multiple operators merge without collisions.
    path:
        Path to a ``.zip`` GTFS archive or to a directory of unzipped GTFS ``.txt`` files.
    """

    key: str
    path: str | Path


@dataclass
class Feed:
    """A single validated, peak-window-filtered GTFS feed.

    Attributes are tidy pandas frames with feed-namespaced ids.
    """

    key: str
    service_date: _dt.date
    peak_start: str
    peak_end: str
    stops: pd.DataFrame  # feed, stop_id, stop_name, stop_lat, stop_lon
    routes: pd.DataFrame  # feed, route_id, route_short_name, route_long_name, route_type, mode
    frequencies: pd.DataFrame  # feed, route_id, direction_id, stop_id, mode, n_departures,
    #                            frequency_vph, headway_min


@dataclass
class GtfsData:
    """One or more feeds merged into a single internal representation."""

    service_date: _dt.date
    peak_start: str
    peak_end: str
    feeds: list[str]
    stops: pd.DataFrame
    routes: pd.DataFrame
    frequencies: pd.DataFrame


@dataclass
class FeedSummary:
    """Human-readable summary returned by :func:`inspect` (Milestone M1)."""

    service_date: _dt.date
    peak_start: str
    peak_end: str
    feeds: list[str]
    n_stops: int
    n_routes: int
    routes_by_mode: dict[str, int]
    n_served_route_stops: int
    median_headway_min: float
    min_headway_min: float
    max_headway_min: float

    def __str__(self) -> str:  # pragma: no cover - presentation only
        modes = ", ".join(f"{m}: {n}" for m, n in sorted(self.routes_by_mode.items()))
        return (
            f"GTFS summary for {', '.join(self.feeds)}\n"
            f"  service date : {self.service_date}  peak {self.peak_start}-{self.peak_end}\n"
            f"  stops        : {self.n_stops}\n"
            f"  routes       : {self.n_routes}  ({modes})\n"
            f"  served (route,dir,stop) pairs in peak : {self.n_served_route_stops}\n"
            f"  headway min/median/max : "
            f"{self.min_headway_min:.1f} / {self.median_headway_min:.1f} / "
            f"{self.max_headway_min:.1f} min"
        )


# Order issues by severity when printing a report.
_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class FeedIssue:
    """A single data-quality finding from :func:`check_feed`.

    Attributes
    ----------
    level:
        ``"error"`` (blocks a usable PTAL run), ``"warning"`` (degrades quality) or
        ``"info"`` (informational).
    code:
        Short machine-readable identifier, e.g. ``"date_out_of_range"``.
    message:
        Human-readable description.
    """

    level: str
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - presentation only
        return f"[{self.level.upper()}] {self.message}"


@dataclass
class FeedReport:
    """The result of :func:`check_feed`: a list of issues for one feed."""

    feed: str
    issues: list[FeedIssue]

    @property
    def errors(self) -> list[FeedIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[FeedIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        """True if the feed has no error-level issues (it can produce a PTAL run)."""
        return not self.errors

    def __str__(self) -> str:  # pragma: no cover - presentation only
        if not self.issues:
            return f"Feed '{self.feed}': no issues found."
        head = f"Feed '{self.feed}': {len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        ordered = sorted(self.issues, key=lambda i: _LEVEL_ORDER.get(i.level, 9))
        return "\n".join([head, *(f"  {i}" for i in ordered)])


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def load_feed(
    source: FeedSource,
    service_date: _dt.date | str,
    *,
    peak_start: str = DEFAULT_PEAK_START,
    peak_end: str = DEFAULT_PEAK_END,
    mode_map: Mapping[int, str] | None = None,
) -> Feed:
    """Load and reduce a single GTFS feed.

    Parameters
    ----------
    source:
        The feed to read (see :class:`FeedSource`).
    service_date:
        Calendar date whose schedule is used, as a ``datetime.date`` or ``"YYYY-MM-DD"``.
    peak_start, peak_end:
        Peak window bounds as ``"HH:MM"`` (or ``"HH:MM:SS"``). The window is half-open
        ``[start, end)``. Defaults to the TfL AM peak (methodology §1.4).
    mode_map:
        Optional override of the ``route_type`` → PTAL mode mapping.

    Returns
    -------
    Feed
        Validated stops, routes and peak-window frequencies, with feed-namespaced ids.
    """
    date = _coerce_date(service_date)
    win_start = _hhmm_to_seconds(peak_start)
    win_end = _hhmm_to_seconds(peak_end)
    if win_end <= win_start:
        raise ValueError(f"peak_end ({peak_end}) must be after peak_start ({peak_start})")
    mode_map = dict(mode_map) if mode_map is not None else DEFAULT_ROUTE_TYPE_MAP

    key = source.key
    reader = _Reader(source.path)
    try:
        tables = _read_tables(reader)
    finally:
        reader.close()
    _validate(tables, feed_key=key)

    # Namespace every id with the feed key up front, so all downstream joins and the
    # eventual cross-feed merge cannot collide. ``direction_id`` is a small integer, not
    # an id, so it is left as-is.
    _namespace_ids(tables, key)

    stops = tables["stops.txt"]
    routes = tables["routes.txt"]
    trips = tables["trips.txt"]
    stop_times = tables["stop_times.txt"]
    calendar = tables.get("calendar.txt")
    calendar_dates = tables.get("calendar_dates.txt")
    freqs = tables.get("frequencies.txt")

    # Which trips actually run on the chosen date?
    active_services = _active_service_ids(calendar, calendar_dates, date)
    active_trips = trips[trips["service_id"].isin(active_services)].copy()

    frequencies = _compute_frequencies(stop_times, active_trips, freqs, win_start, win_end)

    routes = routes.copy()
    routes["mode"] = _map_modes(routes["route_type"], mode_map)
    frequencies = frequencies.merge(routes[["route_id", "mode"]], on="route_id", how="left")

    # Tidy, provenance-tagged outputs.
    stops = stops.assign(feed=key)[["feed", "stop_id", "stop_name", "stop_lat", "stop_lon"]]
    routes = routes.assign(feed=key)[
        ["feed", "route_id", "route_short_name", "route_long_name", "route_type", "mode"]
    ]
    frequencies = frequencies.assign(feed=key)[
        [
            "feed",
            "route_id",
            "direction_id",
            "stop_id",
            "mode",
            "n_departures",
            "frequency_vph",
            "headway_min",
        ]
    ]

    return Feed(
        key=key,
        service_date=date,
        peak_start=peak_start,
        peak_end=peak_end,
        stops=stops.reset_index(drop=True),
        routes=routes.reset_index(drop=True),
        frequencies=frequencies.reset_index(drop=True),
    )


def load_feeds(
    sources: Iterable[FeedSource],
    service_date: _dt.date | str,
    *,
    peak_start: str = DEFAULT_PEAK_START,
    peak_end: str = DEFAULT_PEAK_END,
    mode_map: Mapping[int, str] | None = None,
) -> GtfsData:
    """Load several feeds (the common Indian case: one zip per operator) and merge them.

    Each feed is read independently and its ids are namespaced, so concatenation is a
    safe union. The loop is over feeds (a handful), never over grid points or stops.
    """
    sources = list(sources)
    if not sources:
        raise ValueError("load_feeds requires at least one FeedSource")

    feeds = [
        load_feed(
            s,
            service_date,
            peak_start=peak_start,
            peak_end=peak_end,
            mode_map=mode_map,
        )
        for s in sources
    ]

    stops = pd.concat([f.stops for f in feeds], ignore_index=True)
    routes = pd.concat([f.routes for f in feeds], ignore_index=True)
    frequencies = pd.concat([f.frequencies for f in feeds], ignore_index=True)

    return GtfsData(
        service_date=feeds[0].service_date,
        peak_start=peak_start,
        peak_end=peak_end,
        feeds=[f.key for f in feeds],
        stops=stops,
        routes=routes,
        frequencies=frequencies,
    )


def inspect(
    sources: GtfsData | FeedSource | Iterable[FeedSource],
    service_date: _dt.date | str | None = None,
    *,
    peak_start: str = DEFAULT_PEAK_START,
    peak_end: str = DEFAULT_PEAK_END,
    mode_map: Mapping[int, str] | None = None,
) -> FeedSummary:
    """Return a validated summary of one or more GTFS feeds (Milestone M1).

    Accepts either an already-loaded :class:`GtfsData`, or feed source(s) to load (in
    which case ``service_date`` is required).
    """
    if isinstance(sources, GtfsData):
        data = sources
    else:
        if service_date is None:
            raise ValueError("service_date is required when inspect() is given feed sources")
        if isinstance(sources, FeedSource):
            sources = [sources]
        data = load_feeds(
            sources,
            service_date,
            peak_start=peak_start,
            peak_end=peak_end,
            mode_map=mode_map,
        )

    routes_by_mode = data.routes.groupby("mode").size().to_dict()
    headways = data.frequencies["headway_min"]
    return FeedSummary(
        service_date=data.service_date,
        peak_start=data.peak_start,
        peak_end=data.peak_end,
        feeds=list(data.feeds),
        n_stops=int(len(data.stops)),
        n_routes=int(len(data.routes)),
        routes_by_mode={str(k): int(v) for k, v in routes_by_mode.items()},
        n_served_route_stops=int(len(data.frequencies)),
        median_headway_min=float(headways.median()) if len(headways) else float("nan"),
        min_headway_min=float(headways.min()) if len(headways) else float("nan"),
        max_headway_min=float(headways.max()) if len(headways) else float("nan"),
    )


def check_feed(
    source: FeedSource,
    service_date: _dt.date | str | None = None,
    *,
    mode_map: Mapping[int, str] | None = None,
) -> FeedReport:
    """Report common GTFS data-quality problems for a single feed.

    This surfaces, up front, the issues that otherwise show up as silent or confusing
    results downstream — for example a ``service_date`` outside the feed's calendar
    (which yields zero frequencies), an empty ``direction_id`` (directions merged), or
    stops with missing coordinates.

    Parameters
    ----------
    source:
        The feed to check (see :class:`FeedSource`).
    service_date:
        Optional date to validate against the feed's calendar. When given, the report
        also flags an out-of-range date and a date with no active trips.
    mode_map:
        Optional override of the ``route_type`` → mode mapping (used to flag unmapped
        ``route_type`` codes).

    Returns
    -------
    FeedReport
        The findings. ``report.ok`` is ``True`` when there are no error-level issues.
    """
    reader = _Reader(source.path)
    try:
        tables = _read_tables(reader)
    finally:
        reader.close()

    issues: list[FeedIssue] = []

    # Required files/fields first — without them, deeper checks are meaningless.
    try:
        _validate(tables, feed_key=source.key)
    except GtfsValidationError as exc:
        for line in str(exc).splitlines()[1:]:  # skip the summary line
            issues.append(FeedIssue("error", "missing_required", line.strip(" -")))
        return FeedReport(source.key, issues)

    stops = tables["stops.txt"]
    routes = tables["routes.txt"]
    trips = tables["trips.txt"]
    stop_times = tables["stop_times.txt"]
    calendar = tables.get("calendar.txt")
    calendar_dates = tables.get("calendar_dates.txt")

    # --- calendar span and (optionally) the chosen service date ---
    span = _calendar_span(calendar, calendar_dates)
    if span is not None:
        issues.append(
            FeedIssue(
                "info",
                "calendar_span",
                f"service calendar spans {_fmt_date(span[0])} to {_fmt_date(span[1])}",
            )
        )
    if service_date is not None:
        date = _coerce_date(service_date)
        ymd = int(date.strftime("%Y%m%d"))
        if span is not None and not (span[0] <= ymd <= span[1]):
            issues.append(
                FeedIssue(
                    "error",
                    "date_out_of_range",
                    f"service_date {date} is outside the feed calendar "
                    f"({_fmt_date(span[0])} to {_fmt_date(span[1])})",
                )
            )
        active = _active_service_ids(calendar, calendar_dates, date)
        n_active = int(trips["service_id"].isin(active).sum())
        if n_active == 0:
            issues.append(
                FeedIssue(
                    "error",
                    "no_active_trips",
                    f"service_date {date} ({date.strftime('%A')}) has 0 active trips",
                )
            )
        else:
            issues.append(
                FeedIssue(
                    "info",
                    "active_trips",
                    f"service_date {date} ({date.strftime('%A')}) has {n_active} active trips",
                )
            )

    # --- direction information ---
    if "direction_id" not in trips.columns:
        issues.append(
            FeedIssue(
                "warning",
                "no_direction",
                "trips.txt has no direction_id; the two directions of each route are merged",
            )
        )
    elif int(pd.to_numeric(trips["direction_id"], errors="coerce").notna().sum()) == 0:
        issues.append(
            FeedIssue(
                "warning",
                "blank_direction",
                "direction_id is present but blank for all trips; directions are merged",
            )
        )

    # --- stop coordinates ---
    lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
    lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
    bad_coords = int(
        (
            lat.isna()
            | lon.isna()
            | ((lat == 0) & (lon == 0))
            | (lat.abs() > 90)
            | (lon.abs() > 180)
        ).sum()
    )
    if bad_coords:
        issues.append(
            FeedIssue(
                "warning",
                "bad_coords",
                f"{bad_coords} stop(s) have missing or out-of-range coordinates",
            )
        )

    # --- duplicate ids ---
    for col, df, fname in (("stop_id", stops, "stops"), ("route_id", routes, "routes")):
        n_dup = int(df[col].duplicated().sum())
        if n_dup:
            issues.append(
                FeedIssue(
                    "warning",
                    "duplicate_id",
                    f"{n_dup} duplicate {col} value(s) in {fname}.txt",
                )
            )

    # --- referential integrity ---
    trip_ids = set(trips["trip_id"])
    stop_ids = set(stops["stop_id"])
    orphan_trips = len(trip_ids - set(stop_times["trip_id"]))
    if orphan_trips:
        issues.append(
            FeedIssue(
                "info",
                "trips_without_times",
                f"{orphan_trips} trip(s) have no stop_times entries",
            )
        )
    unknown_trip = int((~stop_times["trip_id"].isin(trip_ids)).sum())
    if unknown_trip:
        issues.append(
            FeedIssue(
                "warning",
                "unknown_trip",
                f"{unknown_trip} stop_times row(s) reference a trip_id not in trips.txt",
            )
        )
    unknown_stop = int((~stop_times["stop_id"].isin(stop_ids)).sum())
    if unknown_stop:
        issues.append(
            FeedIssue(
                "warning",
                "unknown_stop",
                f"{unknown_stop} stop_times row(s) reference a stop_id not in stops.txt",
            )
        )

    # --- stop_times timing ---
    n_rows = len(stop_times)
    dep_sec = (
        _parse_gtfs_time(stop_times["departure_time"])
        if "departure_time" in stop_times.columns
        else pd.Series([pd.NA] * n_rows, dtype="Int64")
    )
    arr_sec = (
        _parse_gtfs_time(stop_times["arrival_time"])
        if "arrival_time" in stop_times.columns
        else pd.Series([pd.NA] * n_rows, dtype="Int64")
    )
    both_missing = int((dep_sec.isna() & arr_sec.isna()).sum())
    if both_missing:
        issues.append(
            FeedIssue(
                "warning",
                "untimed_stop_times",
                f"{both_missing} stop_times row(s) have no departure or arrival time",
            )
        )
    past_midnight = int((dep_sec.fillna(arr_sec) >= 86400).sum())
    if past_midnight:
        issues.append(
            FeedIssue(
                "info",
                "after_midnight",
                f"{past_midnight} stop_times after 24:00:00 (valid GTFS, after-midnight service)",
            )
        )

    # --- unmapped route types ---
    modes = _map_modes(routes["route_type"], mode_map or DEFAULT_ROUTE_TYPE_MAP)
    unmapped = sorted(set(routes.loc[modes == "other", "route_type"].dropna().astype(str)))
    if unmapped:
        issues.append(
            FeedIssue(
                "info",
                "unmapped_route_type",
                f"route_type(s) {unmapped} not recognised; mapped to mode 'other'",
            )
        )

    return FeedReport(source.key, issues)


def _calendar_span(
    calendar: pd.DataFrame | None, calendar_dates: pd.DataFrame | None
) -> tuple[int, int] | None:
    """Earliest and latest YYYYMMDD dates the feed's calendar covers, or ``None``."""
    values: list[float] = []
    if calendar is not None and not calendar.empty:
        values += pd.to_numeric(calendar["start_date"], errors="coerce").dropna().tolist()
        values += pd.to_numeric(calendar["end_date"], errors="coerce").dropna().tolist()
    if calendar_dates is not None and not calendar_dates.empty:
        values += pd.to_numeric(calendar_dates["date"], errors="coerce").dropna().tolist()
    if not values:
        return None
    return int(min(values)), int(max(values))


def _fmt_date(yyyymmdd: int) -> str:
    s = str(int(yyyymmdd))
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _fmt_time(seconds: int) -> str:
    """Seconds-after-midnight to ``HH:MM`` (hours may exceed 24 for late service)."""
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h:02d}:{m:02d}"


# --------------------------------------------------------------------------------------
# Descriptive profiling (stats & distributions)
# --------------------------------------------------------------------------------------

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_HEADWAY_BAND_EDGES = [0, 5, 10, 15, 30, float("inf")]
_HEADWAY_BAND_LABELS = ["<5", "5-10", "10-15", "15-30", "30+"]


@dataclass
class FeedProfile:
    """Descriptive statistics and distributions for one or more feeds (see profile_feeds).

    The attributes are tidy frames/dicts intended for inspection or plotting; ``str()``
    renders a readable text report.
    """

    feeds: list[str]
    service_date: _dt.date
    peak_start: str
    peak_end: str
    totals: dict  # headline counts, service span, busiest hour
    by_mode: pd.DataFrame  # mode, n_routes, n_stops, n_trips, median_headway_min
    hourly_departures: pd.DataFrame  # hour, mode, n_departures (whole service day)
    weekday_trips: pd.DataFrame  # weekday, n_trips (the service week of service_date)
    headway_percentiles: dict  # peak headway p10..p90, min, max (minutes)
    service_level_bands: pd.DataFrame  # headway band, n_routes (per-route best headway)
    routes_per_stop: pd.DataFrame  # stop_id, n_routes (interchange richness)
    stops_per_route: pd.DataFrame  # route_id, n_stops
    extent: dict  # bounding box + centroid

    def __str__(self) -> str:  # pragma: no cover - presentation only
        t = self.totals
        out: list[str] = []
        out.append(f"GTFS profile — {', '.join(self.feeds)}")
        out.append(
            f"  service date {self.service_date} ({self.service_date.strftime('%A')}), "
            f"peak {self.peak_start}-{self.peak_end}"
        )
        out.append(
            f"  stops {t['n_stops']:,} | routes {t['n_routes']:,} | "
            f"active trips {t['n_trips_active']:,} | "
            f"served (route,dir,stop) {t['n_served_route_stops']:,}"
        )
        if t.get("first_departure"):
            out.append(
                f"  service span {t['first_departure']}-{t['last_departure']} | "
                f"busiest hour {t['busiest_hour']:02d}:00"
            )

        out.append("\nBy mode:")
        out.append(f"  {'mode':<10}{'routes':>8}{'stops':>8}{'trips':>9}{'med.hw':>10}")
        for r in self.by_mode.itertuples(index=False):
            hw = "  n/a" if pd.isna(r.median_headway_min) else f"{r.median_headway_min:.1f}m"
            out.append(
                f"  {str(r.mode):<10}{int(r.n_routes):>8}{int(r.n_stops):>8}"
                f"{int(r.n_trips):>9}{hw:>10}"
            )

        out.append("\nDepartures by hour of day:")
        hourly = self.hourly_departures.groupby("hour")["n_departures"].sum()
        peak = hourly.max() if len(hourly) else 0
        for hr in (
            range(int(hourly.index.min()), int(hourly.index.max()) + 1) if len(hourly) else []
        ):
            n = int(hourly.get(hr, 0))
            bar = "#" * round(40 * n / peak) if peak else ""
            out.append(f"  {hr:02d}  {n:>8,}  {bar}")

        out.append(
            "\nTrips by weekday:  "
            + "   ".join(
                f"{r.weekday} {int(r.n_trips):,}"
                for r in self.weekday_trips.itertuples(index=False)
            )
        )

        p = self.headway_percentiles
        out.append(
            f"\nPeak headways (min): p10 {p['p10']:.1f} | median {p['p50']:.1f} | "
            f"p90 {p['p90']:.1f} | min {p['min']:.1f} | max {p['max']:.1f}"
        )
        out.append(
            "Routes by peak service level (best headway):  "
            + " | ".join(
                f"{r.band}: {int(r.n_routes)}"
                for r in self.service_level_bands.itertuples(index=False)
            )
        )

        rps = self.routes_per_stop["n_routes"]
        spr = self.stops_per_route["n_stops"]
        out.append(
            f"\nRoutes per stop: median {rps.median():.0f} | p90 {rps.quantile(0.9):.0f} | "
            f"max {int(rps.max())}"
        )
        out.append(
            f"Stops per route: median {spr.median():.0f} | p90 {spr.quantile(0.9):.0f} | "
            f"max {int(spr.max())}"
        )

        e = self.extent
        out.append(
            f"\nExtent: lat {e['min_lat']:.4f}..{e['max_lat']:.4f}, "
            f"lon {e['min_lon']:.4f}..{e['max_lon']:.4f}"
        )
        return "\n".join(out)


def profile_feed(
    source: FeedSource,
    service_date: _dt.date | str,
    *,
    peak_start: str = DEFAULT_PEAK_START,
    peak_end: str = DEFAULT_PEAK_END,
    mode_map: Mapping[int, str] | None = None,
) -> FeedProfile:
    """Descriptive statistics and distributions for a single feed (see :func:`profile_feeds`)."""
    return profile_feeds(
        [source], service_date, peak_start=peak_start, peak_end=peak_end, mode_map=mode_map
    )


def profile_feeds(
    sources: Iterable[FeedSource],
    service_date: _dt.date | str,
    *,
    peak_start: str = DEFAULT_PEAK_START,
    peak_end: str = DEFAULT_PEAK_END,
    mode_map: Mapping[int, str] | None = None,
) -> FeedProfile:
    """Profile one or more feeds: counts, per-mode breakdown and key distributions.

    Computes, for the chosen service date: headline totals and service span; a per-mode
    table; the whole-day departures-by-hour curve (useful for choosing the peak window);
    trips by weekday; peak headway percentiles and per-route service-level bands;
    interchange richness (routes per stop) and stops per route; and the spatial extent.

    Each feed is read once and reduced to aggregates, so memory stays bounded even on
    large feeds. The loop is over feeds, not grid points.
    """
    sources = list(sources)
    if not sources:
        raise ValueError("profile_feeds requires at least one FeedSource")
    date = _coerce_date(service_date)
    win_start = _hhmm_to_seconds(peak_start)
    win_end = _hhmm_to_seconds(peak_end)
    if win_end <= win_start:
        raise ValueError(f"peak_end ({peak_end}) must be after peak_start ({peak_start})")
    mm = dict(mode_map) if mode_map is not None else DEFAULT_ROUTE_TYPE_MAP

    routes_parts, stops_parts, freq_parts, hourly_parts = [], [], [], []
    rps_parts, spr_parts, tbm_parts, spans = [], [], [], []
    weekday_total: pd.Series | None = None
    n_trips_active = 0
    feeds: list[str] = []

    for src in sources:
        reader = _Reader(src.path)
        try:
            tables = _read_tables(reader)
        finally:
            reader.close()
        _validate(tables, feed_key=src.key)
        _namespace_ids(tables, src.key)
        feeds.append(src.key)

        stops = tables["stops.txt"]
        routes = tables["routes.txt"].copy()
        trips = tables["trips.txt"]
        stop_times = tables["stop_times.txt"]
        calendar = tables.get("calendar.txt")
        calendar_dates = tables.get("calendar_dates.txt")
        freqs = tables.get("frequencies.txt")

        routes["mode"] = _map_modes(routes["route_type"], mm)

        active = _active_service_ids(calendar, calendar_dates, date)
        active_trips = trips[trips["service_id"].isin(active)].copy()
        n_trips_active += len(active_trips)

        # Active stop_times tagged with their route and mode.
        trip_meta = active_trips.merge(routes[["route_id", "mode"]], on="route_id", how="left")[
            ["trip_id", "route_id", "mode"]
        ]
        st = stop_times.merge(trip_meta, on="trip_id", how="inner")
        dep_sec = (
            _parse_gtfs_time(st["departure_time"]) if "departure_time" in st.columns else None
        )
        if "arrival_time" in st.columns:
            arr_sec = _parse_gtfs_time(st["arrival_time"])
            dep_sec = arr_sec if dep_sec is None else dep_sec.fillna(arr_sec)
        st = st.assign(dep_sec=dep_sec).dropna(subset=["dep_sec"])
        st["dep_sec"] = st["dep_sec"].astype("int64")

        # Whole-day departures by hour and mode.
        hour = (st["dep_sec"] // 3600).astype(int)
        hourly_parts.append(
            st.assign(hour=hour)
            .groupby(["hour", "mode"])
            .size()
            .rename("n_departures")
            .reset_index()
        )
        # Interchange richness and route span (distinct routes per stop / stops per route).
        rps_parts.append(
            st[["stop_id", "route_id"]]
            .drop_duplicates()
            .groupby("stop_id")
            .size()
            .rename("n_routes")
            .reset_index()
        )
        spr_parts.append(
            st[["route_id", "stop_id"]]
            .drop_duplicates()
            .groupby("route_id")
            .size()
            .rename("n_stops")
            .reset_index()
        )
        tbm_parts.append(trip_meta.groupby("mode").size().rename("n_trips").reset_index())
        if len(st):
            spans.append((int(st["dep_sec"].min()), int(st["dep_sec"].max())))

        # Peak frequencies (reusing the loader's logic), tagged with mode.
        freq = _compute_frequencies(stop_times, active_trips, freqs, win_start, win_end)
        freq_parts.append(freq.merge(routes[["route_id", "mode"]], on="route_id", how="left"))

        routes_parts.append(routes.assign(feed=src.key))
        stops_parts.append(stops.assign(feed=src.key))

        # Trips per weekday across the service week containing service_date.
        monday = date - _dt.timedelta(days=date.weekday())
        wk = pd.Series(
            {
                _WEEKDAY_NAMES[i]: int(
                    trips["service_id"]
                    .isin(
                        _active_service_ids(
                            calendar, calendar_dates, monday + _dt.timedelta(days=i)
                        )
                    )
                    .sum()
                )
                for i in range(7)
            }
        )
        weekday_total = wk if weekday_total is None else weekday_total.add(wk, fill_value=0)

    routes_all = pd.concat(routes_parts, ignore_index=True)
    stops_all = pd.concat(stops_parts, ignore_index=True)
    freq_all = pd.concat(freq_parts, ignore_index=True)
    hourly_all = (
        pd.concat(hourly_parts, ignore_index=True)
        .groupby(["hour", "mode"], as_index=False)["n_departures"]
        .sum()
    )
    rps_all = pd.concat(rps_parts, ignore_index=True)
    spr_all = pd.concat(spr_parts, ignore_index=True)
    tbm_all = (
        pd.concat(tbm_parts, ignore_index=True).groupby("mode", as_index=False)["n_trips"].sum()
    )

    # Per-mode breakdown.
    n_routes = routes_all.groupby("mode").size().rename("n_routes")
    n_stops = (
        freq_all.drop_duplicates(["mode", "stop_id"]).groupby("mode").size().rename("n_stops")
    )
    median_hw = freq_all.groupby("mode")["headway_min"].median().rename("median_headway_min")
    by_mode = pd.concat([n_routes, n_stops, median_hw], axis=1).reset_index()
    by_mode = by_mode.merge(tbm_all, on="mode", how="left")
    by_mode = by_mode[["mode", "n_routes", "n_stops", "n_trips", "median_headway_min"]]
    by_mode[["n_stops", "n_trips"]] = by_mode[["n_stops", "n_trips"]].fillna(0)

    # Weekday table, in calendar order.
    weekday_total = weekday_total.reindex(_WEEKDAY_NAMES)
    weekday_trips = (
        weekday_total.astype(int).rename_axis("weekday").rename("n_trips").reset_index()
    )

    # Peak headway distribution.
    hw = freq_all["headway_min"]
    headway_percentiles = {
        name: (float(hw.quantile(q)) if len(hw) else float("nan"))
        for name, q in (("p10", 0.1), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9))
    }
    headway_percentiles["min"] = float(hw.min()) if len(hw) else float("nan")
    headway_percentiles["max"] = float(hw.max()) if len(hw) else float("nan")

    # Routes grouped by their best (smallest) peak headway.
    route_min_hw = freq_all.groupby("route_id")["headway_min"].min()
    bands = pd.cut(
        route_min_hw, bins=_HEADWAY_BAND_EDGES, labels=_HEADWAY_BAND_LABELS, right=False
    )
    service_level_bands = (
        bands.value_counts()
        .reindex(_HEADWAY_BAND_LABELS, fill_value=0)
        .rename_axis("band")
        .rename("n_routes")
        .reset_index()
    )

    # Spatial extent.
    lat = pd.to_numeric(stops_all["stop_lat"], errors="coerce")
    lon = pd.to_numeric(stops_all["stop_lon"], errors="coerce")
    extent = {
        "min_lat": float(lat.min()),
        "max_lat": float(lat.max()),
        "min_lon": float(lon.min()),
        "max_lon": float(lon.max()),
        "centroid_lat": float(lat.mean()),
        "centroid_lon": float(lon.mean()),
    }

    overall_span = (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None
    hour_overall = hourly_all.groupby("hour")["n_departures"].sum()
    totals = {
        "feeds": feeds,
        "n_stops": int(len(stops_all)),
        "n_routes": int(len(routes_all)),
        "n_trips_active": int(n_trips_active),
        "n_served_route_stops": int(len(freq_all)),
        "first_departure": _fmt_time(overall_span[0]) if overall_span else None,
        "last_departure": _fmt_time(overall_span[1]) if overall_span else None,
        "busiest_hour": int(hour_overall.idxmax()) if len(hour_overall) else None,
    }

    return FeedProfile(
        feeds=feeds,
        service_date=date,
        peak_start=peak_start,
        peak_end=peak_end,
        totals=totals,
        by_mode=by_mode,
        hourly_departures=hourly_all,
        weekday_trips=weekday_trips,
        headway_percentiles=headway_percentiles,
        service_level_bands=service_level_bands,
        routes_per_stop=rps_all,
        stops_per_route=spr_all,
        extent=extent,
    )


# --------------------------------------------------------------------------------------
# Reading GTFS tables (from a .zip or a directory)
# --------------------------------------------------------------------------------------


class _Reader:
    """Reads GTFS ``.txt`` tables from a zip archive or an unzipped directory.

    GTFS files are located by base name (``stops.txt``), not by exact path, so feeds
    survive two very common real-world quirks: the ``.txt`` files being wrapped in a
    top-level folder inside the zip (``DTC_GTFS/stops.txt``), and macOS ``__MACOSX/``
    resource-fork junk. Only the columns PTAL needs are read (``usecols``), which is the
    main memory/time saving on the large ``stop_times`` table.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._zip: zipfile.ZipFile | None = None
        if self.path.is_file() and self.path.suffix.lower() == ".zip":
            self._zip = zipfile.ZipFile(self.path)
            entries = self._zip.namelist()
        elif self.path.is_dir():
            # Search recursively so a feed unzipped into a subfolder still resolves.
            entries = [p.relative_to(self.path).as_posix() for p in self.path.rglob("*.txt")]
        else:
            raise FileNotFoundError(f"GTFS path is not a .zip or directory: {self.path}")
        # Map each base file name to its actual entry, preferring the shallowest match.
        self._members = self._index_members(entries)

    @staticmethod
    def _index_members(entries: list[str]) -> dict[str, str]:
        # base name -> (actual entry, folder depth); shallowest depth wins.
        best: dict[str, tuple[str, int]] = {}
        for entry in entries:
            posix = entry.replace("\\", "/")
            if posix.endswith("/") or "__MACOSX/" in posix:
                continue  # directory entry or macOS junk folder
            base = posix.rsplit("/", 1)[-1]
            if not base or base.startswith("._"):
                continue  # empty or macOS resource-fork file
            depth = posix.count("/")
            # A file at the root wins over the same name nested in a subfolder.
            if base not in best or depth < best[base][1]:
                best[base] = (entry, depth)
        return {base: entry for base, (entry, _) in best.items()}

    def has(self, name: str) -> bool:
        return name in self._members

    def _open(self, name: str):
        entry = self._members[name]
        if self._zip is not None:
            return self._zip.open(entry)
        return open(self.path / entry, "rb")

    def read(
        self,
        name: str,
        want: tuple[str, ...],
        dtype: Mapping[str, str],
    ) -> pd.DataFrame | None:
        """Read ``name`` keeping only the ``want`` columns that exist; ``None`` if absent."""
        if not self.has(name):
            return None
        # Peek at the header so ``usecols`` only ever names columns that exist (GTFS makes
        # many columns optional). ``utf-8-sig`` strips a BOM if present.
        with self._open(name) as fh:
            header = pd.read_csv(fh, nrows=0, encoding="utf-8-sig", skipinitialspace=True)
        present = [c.strip() for c in header.columns]
        usecols = [c for c in want if c in present]
        dtypes = {c: dtype[c] for c in usecols if c in dtype}
        with self._open(name) as fh:
            df = pd.read_csv(
                fh,
                usecols=usecols,
                dtype=dtypes,
                encoding="utf-8-sig",
                skipinitialspace=True,
            )
        df.columns = [c.strip() for c in df.columns]
        return df

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


# Id columns are read as pandas "string" so values keep leading zeros and namespace
# cleanly; times stay as strings until parsed; coordinates are floats.
def _read_tables(reader: _Reader) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}

    tables["stops.txt"] = reader.read(
        "stops.txt",
        want=("stop_id", "stop_name", "stop_lat", "stop_lon"),
        dtype={
            "stop_id": "string",
            "stop_name": "string",
            "stop_lat": "float64",
            "stop_lon": "float64",
        },
    )
    tables["routes.txt"] = reader.read(
        "routes.txt",
        want=("route_id", "route_short_name", "route_long_name", "route_type"),
        dtype={
            "route_id": "string",
            "route_short_name": "string",
            "route_long_name": "string",
            "route_type": "string",
        },
    )
    tables["trips.txt"] = reader.read(
        "trips.txt",
        want=("trip_id", "route_id", "service_id", "direction_id"),
        dtype={
            "trip_id": "string",
            "route_id": "string",
            "service_id": "string",
            "direction_id": "Int64",
        },
    )
    tables["stop_times.txt"] = reader.read(
        "stop_times.txt",
        want=("trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"),
        dtype={
            "trip_id": "string",
            "stop_id": "string",
            "arrival_time": "string",
            "departure_time": "string",
            "stop_sequence": "Int64",
        },
    )
    calendar = reader.read(
        "calendar.txt",
        want=(
            "service_id",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "start_date",
            "end_date",
        ),
        dtype={"service_id": "string"},
    )
    if calendar is not None:
        tables["calendar.txt"] = calendar
    calendar_dates = reader.read(
        "calendar_dates.txt",
        want=("service_id", "date", "exception_type"),
        dtype={"service_id": "string"},
    )
    if calendar_dates is not None:
        tables["calendar_dates.txt"] = calendar_dates
    freqs = reader.read(
        "frequencies.txt",
        want=("trip_id", "start_time", "end_time", "headway_secs", "exact_times"),
        dtype={"trip_id": "string"},
    )
    if freqs is not None:
        tables["frequencies.txt"] = freqs

    return tables


def _validate(tables: Mapping[str, pd.DataFrame | None], feed_key: str) -> None:
    """Check required files and fields are present; raise a clear error otherwise."""
    problems: list[str] = []
    for fname, fields in _REQUIRED_FIELDS.items():
        df = tables.get(fname)
        if df is None:
            problems.append(f"missing required file {fname}")
            continue
        missing = [f for f in fields if f not in df.columns]
        if missing:
            problems.append(f"{fname} missing field(s): {', '.join(missing)}")

    st = tables.get("stop_times.txt")
    if st is not None and not ({"departure_time", "arrival_time"} & set(st.columns)):
        problems.append("stop_times.txt needs departure_time or arrival_time")

    if "calendar.txt" not in tables and "calendar_dates.txt" not in tables:
        problems.append("feed has neither calendar.txt nor calendar_dates.txt")

    if problems:
        raise GtfsValidationError(
            f"GTFS feed '{feed_key}' failed validation:\n  - " + "\n  - ".join(problems)
        )


# --------------------------------------------------------------------------------------
# Helpers: namespacing, dates, time parsing, mode mapping
# --------------------------------------------------------------------------------------


def _namespace_ids(tables: dict[str, pd.DataFrame], key: str) -> None:
    """Prefix every id column with ``key + ':'`` so feeds never collide."""
    prefix = f"{key}:"

    def ns(df: pd.DataFrame | None, cols: tuple[str, ...]) -> None:
        if df is None:
            return
        for col in cols:
            if col in df.columns:
                df[col] = prefix + df[col].astype("string")

    ns(tables.get("stops.txt"), ("stop_id",))
    ns(tables.get("routes.txt"), ("route_id",))
    ns(tables.get("trips.txt"), ("trip_id", "route_id", "service_id"))
    ns(tables.get("stop_times.txt"), ("trip_id", "stop_id"))
    ns(tables.get("calendar.txt"), ("service_id",))
    ns(tables.get("calendar_dates.txt"), ("service_id",))
    ns(tables.get("frequencies.txt"), ("trip_id",))


def _coerce_date(value: _dt.date | str) -> _dt.date:
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))


def _hhmm_to_seconds(value: str) -> int:
    """Convert ``"HH:MM"`` or ``"HH:MM:SS"`` to seconds after midnight."""
    parts = [int(p) for p in str(value).split(":")]
    if len(parts) == 2:
        h, m, s = parts[0], parts[1], 0
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise ValueError(f"invalid time {value!r}; expected HH:MM or HH:MM:SS")
    return h * 3600 + m * 60 + s


def _parse_gtfs_time(series: pd.Series) -> pd.Series:
    """Vectorised parse of GTFS times to seconds after midnight (nullable ``Int64``).

    GTFS times may exceed 24h (e.g. ``"25:30:00"`` for after-midnight service); this is
    preserved. Blank/invalid values become NA.
    """
    parts = series.astype("string").str.strip().str.split(":", expand=True)
    parts = parts.reindex(columns=[0, 1, 2])
    h = pd.to_numeric(parts[0], errors="coerce")
    m = pd.to_numeric(parts[1], errors="coerce")
    s = pd.to_numeric(parts[2], errors="coerce")
    return (h * 3600 + m * 60 + s).astype("Int64")


def _active_service_ids(
    calendar: pd.DataFrame | None,
    calendar_dates: pd.DataFrame | None,
    date: _dt.date,
) -> set[str]:
    """Service ids running on ``date``: weekly pattern minus/plus calendar exceptions."""
    yyyymmdd = int(date.strftime("%Y%m%d"))
    weekday = date.strftime("%A").lower()  # 'monday' .. 'sunday'

    active: set[str] = set()
    if calendar is not None and not calendar.empty and weekday in calendar.columns:
        in_range = (pd.to_numeric(calendar["start_date"], errors="coerce") <= yyyymmdd) & (
            pd.to_numeric(calendar["end_date"], errors="coerce") >= yyyymmdd
        )
        runs_today = pd.to_numeric(calendar[weekday], errors="coerce") == 1
        active = set(calendar.loc[in_range & runs_today, "service_id"].dropna())

    if calendar_dates is not None and not calendar_dates.empty:
        today = calendar_dates[pd.to_numeric(calendar_dates["date"], errors="coerce") == yyyymmdd]
        exc = pd.to_numeric(today["exception_type"], errors="coerce")
        added = set(today.loc[exc == 1, "service_id"].dropna())
        removed = set(today.loc[exc == 2, "service_id"].dropna())
        active = (active | added) - removed

    return active


def _map_modes(route_type: pd.Series, mode_map: Mapping[int, str]) -> pd.Series:
    """Map GTFS ``route_type`` codes to PTAL mode names.

    Uses ``mode_map`` for the standard base codes, falls back to the extended-GTFS code
    ranges (Google's 100–1700 scheme), and labels anything unknown ``"other"``.
    """
    codes = pd.to_numeric(route_type, errors="coerce")

    def to_mode(code: float) -> str:
        if pd.isna(code):
            return "other"
        code = int(code)
        if code in mode_map:
            return mode_map[code]
        # Extended GTFS route types (broad buckets only; full mapping moves to config).
        if 100 <= code < 200:
            return "rail"
        if 200 <= code < 300:
            return "bus"  # coach services
        if 400 <= code < 500:
            return "metro"
        if 700 <= code < 800:
            return "bus"
        if 900 <= code < 1000:
            return "tram"
        return "other"

    return codes.map(to_mode).astype("string")


# --------------------------------------------------------------------------------------
# Frequency / headway computation
# --------------------------------------------------------------------------------------


def _compute_frequencies(
    stop_times: pd.DataFrame,
    active_trips: pd.DataFrame,
    freqs: pd.DataFrame | None,
    win_start: int,
    win_end: int,
) -> pd.DataFrame:
    """Departures, frequency (veh/h) and headway (min) per (route, direction, stop).

    Frequency-based trips (those listed in ``frequencies.txt``) are counted from their
    headway definitions; all other active trips are counted directly from ``stop_times``.
    """
    window_hours = (win_end - win_start) / 3600.0

    # Carry direction onto every stop_time; the inner join also drops inactive trips,
    # so we only ever parse times for trips that run on the chosen date.
    direction = active_trips.get("direction_id")
    meta = active_trips[["trip_id", "route_id"]].copy()
    meta["direction_id"] = direction.fillna(0).astype("int64") if direction is not None else 0
    st = stop_times.merge(meta, on="trip_id", how="inner")

    # Departure time, falling back to arrival when departure is blank (interpolated stop).
    dep_sec = _parse_gtfs_time(st["departure_time"]) if "departure_time" in st else None
    if "arrival_time" in st.columns:
        arr_sec = _parse_gtfs_time(st["arrival_time"])
        dep_sec = arr_sec if dep_sec is None else dep_sec.fillna(arr_sec)
    st = st.assign(dep_sec=dep_sec).dropna(subset=["dep_sec"])
    st["dep_sec"] = st["dep_sec"].astype("int64")

    freq_trip_ids = set(freqs["trip_id"]) if freqs is not None and not freqs.empty else set()

    # Schedule-based: a departure is a stop_time falling inside the half-open window.
    sched = st[~st["trip_id"].isin(freq_trip_ids)]
    sched = sched[(sched["dep_sec"] >= win_start) & (sched["dep_sec"] < win_end)]
    counts = (
        sched.groupby(["route_id", "direction_id", "stop_id"]).size().rename("n_departures")
    ).reset_index()

    parts = [counts]
    if freq_trip_ids:
        parts.append(
            _frequency_based_counts(
                st[st["trip_id"].isin(freq_trip_ids)], freqs, win_start, win_end
            )
        )

    out = (
        pd.concat(parts, ignore_index=True)
        .groupby(["route_id", "direction_id", "stop_id"], as_index=False)["n_departures"]
        .sum()
    )
    out = out[out["n_departures"] > 0].copy()
    out["frequency_vph"] = out["n_departures"] / window_hours
    out["headway_min"] = 60.0 / out["frequency_vph"]
    return out


def _frequency_based_counts(
    st_freq: pd.DataFrame,
    freqs: pd.DataFrame,
    win_start: int,
    win_end: int,
) -> pd.DataFrame:
    """Count peak-window departures for ``frequencies.txt`` trips.

    A frequency entry means "this trip departs every ``headway_secs`` between
    ``start_time`` and ``end_time``". Each instance's time at a given stop is the instance
    start plus that stop's offset from the trip's first stop. We expand instance start
    times (vectorised with numpy) and count those landing in the window per stop.

    The Python loop here is over ``frequencies.txt`` rows only (typically a few dozen),
    never over grid points or stops, so it is not on the PTAL hot path.
    """
    # Each stop's offset from its trip's first departure.
    t0 = st_freq.groupby("trip_id")["dep_sec"].transform("min")
    st_freq = st_freq.assign(offset=st_freq["dep_sec"] - t0)

    f = freqs.copy()
    f["start_sec"] = _parse_gtfs_time(f["start_time"]).astype("Int64")
    f["end_sec"] = _parse_gtfs_time(f["end_time"]).astype("Int64")
    f["headway_secs"] = pd.to_numeric(f["headway_secs"], errors="coerce")

    cols = ["route_id", "direction_id", "stop_id", "n_departures"]
    parts: list[pd.DataFrame] = []
    for row in f.itertuples(index=False):
        rows = st_freq[st_freq["trip_id"] == row.trip_id]
        if rows.empty or pd.isna(row.headway_secs) or row.headway_secs <= 0:
            continue
        if pd.isna(row.start_sec) or pd.isna(row.end_sec):
            continue
        instances = np.arange(int(row.start_sec), int(row.end_sec), int(row.headway_secs))
        if instances.size == 0:
            continue
        # (n_instances, n_stops) matrix of departure times, then count those in-window.
        dep = instances[:, None] + rows["offset"].to_numpy()[None, :]
        n = ((dep >= win_start) & (dep < win_end)).sum(axis=0)
        parts.append(
            pd.DataFrame(
                {
                    "route_id": rows["route_id"].to_numpy(),
                    "direction_id": rows["direction_id"].to_numpy(),
                    "stop_id": rows["stop_id"].to_numpy(),
                    "n_departures": n,
                }
            )
        )

    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts, ignore_index=True)
