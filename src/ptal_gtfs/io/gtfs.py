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


# --------------------------------------------------------------------------------------
# Reading GTFS tables (from a .zip or a directory)
# --------------------------------------------------------------------------------------


class _Reader:
    """Reads GTFS ``.txt`` tables from a zip archive or an unzipped directory.

    Only the columns PTAL needs are read (``usecols``), which is the main memory/time
    saving on the large ``stop_times`` table.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._zip: zipfile.ZipFile | None = None
        if self.path.is_file() and self.path.suffix.lower() == ".zip":
            self._zip = zipfile.ZipFile(self.path)
        elif not self.path.is_dir():
            raise FileNotFoundError(f"GTFS path is not a .zip or directory: {self.path}")

    def _names(self) -> set[str]:
        if self._zip is not None:
            return set(self._zip.namelist())
        return {p.name for p in self.path.iterdir()}

    def has(self, name: str) -> bool:
        return name in self._names()

    def _open(self, name: str):
        if self._zip is not None:
            return self._zip.open(name)
        return open(self.path / name, "rb")

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
