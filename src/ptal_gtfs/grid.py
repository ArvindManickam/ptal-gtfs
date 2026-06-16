"""Study area (boundary) and POI grid generation.

The study area is a polygon — supplied as a file or derived from the GTFS stops — that
bounds where PTAL is computed. A regular point grid over the area provides the Points of
Interest (POIs) for which PTAL is evaluated (methodology §1.1, default 100 m spacing).

Geometry is held in two coordinate systems: WGS84 (lon/lat, ``EPSG:4326``) for I/O, and
a metric UTM CRS (auto-selected from the area) for grid spacing and distances in metres.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

WGS84 = "EPSG:4326"


@dataclass
class StudyArea:
    """A bounded study area in both geographic and metric coordinates.

    Attributes
    ----------
    polygon:
        The area boundary in WGS84 (lon/lat).
    crs_metric:
        A metric CRS (UTM) auto-selected for the area; distances in it are metres.
    polygon_metric:
        ``polygon`` projected to ``crs_metric``.
    """

    polygon: BaseGeometry
    crs_metric: object
    polygon_metric: BaseGeometry

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """WGS84 bounding box ``(min_lon, min_lat, max_lon, max_lat)``."""
        return self.polygon.bounds


def _study_area_from_gdf(gdf: gpd.GeoDataFrame) -> StudyArea:
    """Build a :class:`StudyArea` from a WGS84 GeoDataFrame (dissolving its geometries)."""
    gdf = gdf.to_crs(WGS84)
    polygon = gdf.union_all()
    crs_metric = gdf.estimate_utm_crs()
    polygon_metric = gpd.GeoSeries([polygon], crs=WGS84).to_crs(crs_metric).iloc[0]
    return StudyArea(polygon=polygon, crs_metric=crs_metric, polygon_metric=polygon_metric)


def load_boundary(source: str | Path) -> StudyArea:
    """Load a study-area boundary from a polygon file.

    Parameters
    ----------
    source:
        Path to a polygon file (GeoJSON/GPKG/shapefile, or any vector format GeoPandas
        can read). Multiple features are dissolved into a single boundary.

    Raises
    ------
    FileNotFoundError
        If ``source`` does not exist.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"boundary file not found: {path}")
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    return _study_area_from_gdf(gdf)


def boundary_from_stops(stops: pd.DataFrame, *, buffer_m: float = 500.0) -> StudyArea:
    """Derive a study area from GTFS stops: their convex hull buffered by ``buffer_m``.

    Parameters
    ----------
    stops:
        A frame with ``stop_lon``/``stop_lat`` columns (e.g. ``load_feeds(...).stops``).
    buffer_m:
        Buffer added around the convex hull, in metres.
    """
    pts = gpd.GeoSeries(gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]), crs=WGS84)
    crs_metric = pts.estimate_utm_crs()
    hull_metric = pts.to_crs(crs_metric).union_all().convex_hull.buffer(buffer_m)
    polygon = gpd.GeoSeries([hull_metric], crs=crs_metric).to_crs(WGS84).iloc[0]
    return StudyArea(polygon=polygon, crs_metric=crs_metric, polygon_metric=hull_metric)


def make_grid(
    area: StudyArea, *, spacing_m: float = 100.0, cell: bool = False
) -> gpd.GeoDataFrame:
    """Generate a regular grid of POIs clipped to the study area.

    Each grid location is a Point of Interest. By default the geometry is the **centroid
    point**; with ``cell=True`` it is the **square cell** of side ``spacing_m`` (e.g. a
    100 m cell = 10,000 m²) centred on that centroid — handy for choropleth maps. Either
    way the centroid is kept in ``x``/``y`` (metres) and ``lon``/``lat`` (WGS84), and a
    given area/spacing always yields the same ``poi_id``s, so a centroid grid and a cell
    grid share ids and can be joined (cells for the fill, centroids for routing/labels).

    Parameters
    ----------
    area:
        The study area.
    spacing_m:
        Grid spacing in metres (methodology default 100 m).
    cell:
        If ``True``, the geometry is the square cell polygon; otherwise the centroid point.

    Returns
    -------
    geopandas.GeoDataFrame
        Columns ``poi_id``, ``geometry`` (centroid point or square cell), ``x``/``y``
        (centroid, metres) and ``lon``/``lat`` (centroid, WGS84), in ``area.crs_metric``.
    """
    min_x, min_y, max_x, max_y = area.polygon_metric.bounds
    xs = np.arange(min_x, max_x + spacing_m, spacing_m)
    ys = np.arange(min_y, max_y + spacing_m, spacing_m)
    grid_x, grid_y = np.meshgrid(xs, ys)

    centroids = gpd.GeoSeries(
        gpd.points_from_xy(grid_x.ravel(), grid_y.ravel()), crs=area.crs_metric
    )
    # Keep cells/points whose centroid is inside the boundary (one vectorised test).
    centroids = centroids[centroids.within(area.polygon_metric).to_numpy()]
    cx = centroids.x.to_numpy()
    cy = centroids.y.to_numpy()

    if cell:
        half = spacing_m / 2
        geometry = shapely.box(cx - half, cy - half, cx + half, cy + half)  # vectorised
    else:
        geometry = centroids.values

    lonlat = centroids.to_crs(WGS84)
    return gpd.GeoDataFrame(
        {
            "poi_id": np.arange(len(cx), dtype="int64"),
            "geometry": geometry,
            "x": cx,
            "y": cy,
            "lon": lonlat.x.to_numpy(),
            "lat": lonlat.y.to_numpy(),
        },
        geometry="geometry",
        crs=area.crs_metric,
    )
