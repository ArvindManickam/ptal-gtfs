"""Tests for study-area and grid generation (``ptal_gtfs.grid``)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from ptal_gtfs.grid import WGS84, boundary_from_stops, load_boundary, make_grid

BOUNDARY = Path(__file__).parent / "fixtures" / "boundary.geojson"


def test_load_boundary_from_file():
    area = load_boundary(BOUNDARY)
    assert area.polygon.is_valid
    assert area.polygon_metric.area > 0  # projected to metres
    assert "4326" not in str(area.crs_metric)  # a metric (UTM) CRS, not lon/lat


def test_make_grid_spacing_and_containment():
    area = load_boundary(BOUNDARY)
    grid = make_grid(area, spacing_m=200)
    assert len(grid) > 0
    assert grid["poi_id"].is_unique
    # Every grid point lies inside the study-area polygon.
    assert grid.geometry.within(area.polygon_metric).all()
    # Has both metric and WGS84 coordinates.
    assert {"x", "y", "lon", "lat"}.issubset(grid.columns)


def test_finer_spacing_gives_more_points():
    area = load_boundary(BOUNDARY)
    assert len(make_grid(area, spacing_m=100)) > len(make_grid(area, spacing_m=300))


def test_boundary_from_stops_contains_stops():
    stops = pd.DataFrame(
        {"stop_lon": [77.589, 77.593, 77.591], "stop_lat": [12.969, 12.969, 12.973]}
    )
    area = boundary_from_stops(stops, buffer_m=100)
    pts = gpd.GeoSeries(gpd.points_from_xy(stops.stop_lon, stops.stop_lat), crs=WGS84)
    assert pts.within(area.polygon).all()
