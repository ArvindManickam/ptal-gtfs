"""Tests for the GTFS loader (``ptal_gtfs.io.gtfs``).

The fixture ``tests/fixtures/mini_gtfs`` is a tiny synthetic feed designed so the
peak-window departure counts are obvious by hand. For service date 2024-01-03 (a
Wednesday) and the TfL peak window 08:15-09:15 (half-open):

  R1 dir 0 -> S1: T1 08:20, T2 08:40, T3 09:00            = 3   (T4 08:00 is before window)
  R1 dir 0 -> S2: T1 08:30, T2 08:50, T3 09:10            = 3
  R1 dir 1 -> S2: T5 08:30, T6 09:00                       = 2
  R1 dir 1 -> S1: T5 08:45                                 = 1   (T6 09:15 hits the open end)
  R2 dir 0 -> S3: T7 08:25, T8 08:55                       = 2
  R2 dir 0 -> S4: T7 08:35, T8 09:05                       = 2

Trip T9 runs only on WEEKEND service, so it must be excluded on a Wednesday.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ptal_gtfs import (
    FeedSource,
    GtfsValidationError,
    check_feed,
    inspect,
    load_feed,
    load_feeds,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini_gtfs"
SERVICE_DATE = "2024-01-03"  # Wednesday


def _freq(feed, route_id, direction, stop_id):
    """Return n_departures for one (route, direction, stop) row, or 0 if absent."""
    f = feed.frequencies
    row = f[(f.route_id == route_id) & (f.direction_id == direction) & (f.stop_id == stop_id)]
    return 0 if row.empty else int(row["n_departures"].iloc[0])


def test_load_feed_counts_peak_departures():
    feed = load_feed(FeedSource("bus", FIXTURE), SERVICE_DATE)

    assert _freq(feed, "bus:R1", 0, "bus:S1") == 3
    assert _freq(feed, "bus:R1", 0, "bus:S2") == 3
    assert _freq(feed, "bus:R1", 1, "bus:S2") == 2
    assert _freq(feed, "bus:R1", 1, "bus:S1") == 1  # 09:15 excluded (half-open window)
    assert _freq(feed, "bus:R2", 0, "bus:S3") == 2
    assert _freq(feed, "bus:R2", 0, "bus:S4") == 2


def test_frequency_and_headway_derived_from_counts():
    feed = load_feed(FeedSource("bus", FIXTURE), SERVICE_DATE)
    row = feed.frequencies[
        (feed.frequencies.route_id == "bus:R1")
        & (feed.frequencies.direction_id == 0)
        & (feed.frequencies.stop_id == "bus:S1")
    ].iloc[0]
    # 3 departures in a 1-hour window -> 3 veh/h -> 20 min headway.
    assert row["frequency_vph"] == pytest.approx(3.0)
    assert row["headway_min"] == pytest.approx(20.0)


def test_weekend_only_trip_excluded_on_weekday():
    # T9 is the only trip that would add a 4th departure at S1; it is WEEKEND service.
    feed = load_feed(FeedSource("bus", FIXTURE), SERVICE_DATE)
    assert _freq(feed, "bus:R1", 0, "bus:S1") == 3


def test_ids_are_namespaced_by_feed_key():
    feed = load_feed(FeedSource("xyz", FIXTURE), SERVICE_DATE)
    assert set(feed.stops.stop_id) == {"xyz:S1", "xyz:S2", "xyz:S3", "xyz:S4"}
    assert feed.routes.route_id.str.startswith("xyz:").all()
    assert (feed.stops.feed == "xyz").all()


def test_route_type_maps_to_mode():
    feed = load_feed(FeedSource("bus", FIXTURE), SERVICE_DATE)
    modes = dict(zip(feed.routes.route_id, feed.routes["mode"], strict=True))
    assert modes["bus:R1"] == "bus"  # route_type 3
    assert modes["bus:R2"] == "rail"  # route_type 2


def test_multiple_feeds_merge_without_id_collision():
    # Loading the same fixture under two keys simulates two operators that happen to use
    # the same internal ids. Namespacing must keep them distinct.
    data = load_feeds(
        [FeedSource("a", FIXTURE), FeedSource("b", FIXTURE)],
        SERVICE_DATE,
    )
    assert data.feeds == ["a", "b"]
    assert {"a:S1", "b:S1"} <= set(data.stops.stop_id)
    assert len(data.stops) == 8  # 4 stops x 2 feeds, no collisions collapsed
    # Each feed contributes the same set of served (route, dir, stop) pairs.
    single = load_feed(FeedSource("a", FIXTURE), SERVICE_DATE)
    assert len(data.frequencies) == 2 * len(single.frequencies)


def test_custom_peak_window_changes_counts():
    # A narrow 08:15-08:35 window keeps only the earliest R1 dir0 departure at S1 (08:20);
    # T2 at 08:40 now falls outside, so the count drops from 3 to 1.
    feed = load_feed(
        FeedSource("bus", FIXTURE), SERVICE_DATE, peak_start="08:15", peak_end="08:35"
    )
    assert _freq(feed, "bus:R1", 0, "bus:S1") == 1


def test_inspect_summary():
    summary = inspect(FeedSource("bus", FIXTURE), SERVICE_DATE)
    assert summary.n_stops == 4
    assert summary.n_routes == 2
    assert summary.routes_by_mode == {"bus": 1, "rail": 1}
    assert summary.n_served_route_stops == 6


def test_load_from_zip(tmp_path):
    # The reader must accept a .zip identically to a directory.
    import zipfile

    zip_path = tmp_path / "mini.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for txt in FIXTURE.glob("*.txt"):
            zf.write(txt, arcname=txt.name)

    from_dir = load_feed(FeedSource("bus", FIXTURE), SERVICE_DATE)
    from_zip = load_feed(FeedSource("bus", zip_path), SERVICE_DATE)
    assert _freq(from_zip, "bus:R1", 0, "bus:S1") == _freq(from_dir, "bus:R1", 0, "bus:S1")


def test_load_from_zip_with_wrapper_folder_and_macosx_junk(tmp_path):
    # Real-world feeds often wrap the .txt files in a top-level folder and (when zipped
    # on macOS) carry __MACOSX/._* resource-fork junk. The reader must see through both.
    import zipfile

    zip_path = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("FEED/", "")  # directory entry
        for txt in FIXTURE.glob("*.txt"):
            zf.write(txt, arcname=f"FEED/{txt.name}")
            zf.writestr(f"FEED/__MACOSX/._{txt.name}", b"junk-resource-fork")

    feed = load_feed(FeedSource("bus", zip_path), SERVICE_DATE)
    assert _freq(feed, "bus:R1", 0, "bus:S1") == 3
    assert set(feed.stops.stop_id) == {"bus:S1", "bus:S2", "bus:S3", "bus:S4"}


def test_invalid_peak_window_rejected():
    with pytest.raises(ValueError):
        load_feed(FeedSource("bus", FIXTURE), SERVICE_DATE, peak_start="09:15", peak_end="08:15")

def test_missing_required_file_raises(tmp_path):
    # A directory with only stops.txt must fail validation with a clear message.
    (tmp_path / "stops.txt").write_text("stop_id,stop_lat,stop_lon\nS1,12.0,77.0\n")
    with pytest.raises(GtfsValidationError):
        load_feed(FeedSource("broken", tmp_path), SERVICE_DATE)


# --- check_feed (quality report) ---------------------------------------------------


def _codes(report):
    return {i.code for i in report.issues}


def test_check_feed_clean_fixture_has_no_errors_or_warnings():
    report = check_feed(FeedSource("bus", FIXTURE), SERVICE_DATE)
    assert report.ok
    assert report.errors == []
    assert report.warnings == []
    # The clean fixture still reports informational items (calendar span, active trips).
    assert "calendar_span" in _codes(report)
    assert "active_trips" in _codes(report)


def test_check_feed_flags_date_outside_calendar():
    report = check_feed(FeedSource("bus", FIXTURE), "2030-01-01")
    assert not report.ok
    codes = _codes(report)
    assert "date_out_of_range" in codes
    assert "no_active_trips" in codes


def test_check_feed_missing_required_file_is_error(tmp_path):
    (tmp_path / "stops.txt").write_text("stop_id,stop_lat,stop_lon\nS1,12.0,77.0\n")
    report = check_feed(FeedSource("broken", tmp_path))
    assert not report.ok
    assert all(i.level == "error" for i in report.errors)


def test_check_feed_detects_quality_issues(tmp_path):
    # Craft a tiny feed with: a duplicate stop_id, a (0,0) coordinate, and a
    # direction_id column that is present but entirely blank.
    (tmp_path / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "A,Stop A,12.90,77.50\n"
        "A,Stop A dup,12.91,77.51\n"
        "B,Bad Coord,0,0\n"
    )
    (tmp_path / "routes.txt").write_text("route_id,route_short_name,route_type\nR,1,3\n")
    (tmp_path / "trips.txt").write_text("route_id,service_id,trip_id,direction_id\nR,WK,T1,\n")
    (tmp_path / "calendar.txt").write_text(
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,1,1,20240101,20241231\n"
    )
    (tmp_path / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T1,08:20:00,08:20:00,A,1\n"
        "T1,08:30:00,08:30:00,B,2\n"
    )

    report = check_feed(FeedSource("messy", tmp_path))
    codes = _codes(report)
    assert "duplicate_id" in codes
    assert "bad_coords" in codes
    assert "blank_direction" in codes
