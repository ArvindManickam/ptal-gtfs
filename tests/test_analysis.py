"""Tests for the top-level workflow (``ptal_gtfs.analysis``)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmnx as ox
import shapely

from ptal_gtfs import FeedSource, PTALAnalysis, PTALResult
from ptal_gtfs.analysis import _to_feeds

FIXTURES = Path(__file__).parent / "fixtures"
BOUNDARY = FIXTURES / "boundary.geojson"
MINI = FIXTURES / "mini_gtfs"


def test_to_feeds_accepts_many_forms():
    assert _to_feeds("a/b/city.zip") == [FeedSource("city", "a/b/city.zip")]
    assert _to_feeds({"dtc": "x.zip", "dmrc": "y.zip"}) == [
        FeedSource("dtc", "x.zip"),
        FeedSource("dmrc", "y.zip"),
    ]
    src = FeedSource("k", "z.zip")
    assert _to_feeds(src) == [src]
    assert _to_feeds([src, "p.zip"]) == [src, FeedSource("p", "p.zip")]


def test_from_files_resolves_profile_and_feeds():
    analysis = PTALAnalysis.from_files(
        gtfs="data/city.zip", service_date="2024-01-03", profile="india"
    )
    assert analysis.feeds == [FeedSource("city", "data/city.zip")]
    assert analysis.profile.name == "india"
    assert analysis.boundary is None  # -> hull
    assert analysis.osm == "overpass"


def _result_fixture():
    cells = gpd.GeoDataFrame(
        {
            "poi_id": [0, 1],
            "ai_bus": [3.0, 0.0],
            "ai": [3.0, 0.0],
            "ptal_band": ["1b", "0"],
            "geometry": [shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1)],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    return PTALResult(grid=cells, manifest={"ptal_gtfs_version": "0.0.1", "summary": {}})


def test_result_writers(tmp_path):
    res = _result_fixture()
    assert dict(res.bands) == {"0": 1, "1b": 1}

    res.to_csv(tmp_path / "r.csv")
    res.to_geopackage(tmp_path / "r.gpkg")
    res.to_manifest(tmp_path / "r.yaml")
    res.plot_map(tmp_path / "r.html")
    for name in ("r.csv", "r.gpkg", "r.yaml", "r.html"):
        assert (tmp_path / name).exists()

    res.save(tmp_path / "city")
    for suffix in (".gpkg", ".csv", "_run.yaml"):
        assert (tmp_path / f"city{suffix}").exists()


def _dense_graphml(path):
    """A dense WGS84 lattice covering the boundary fixture, saved as GraphML."""
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    lons = [77.588 + i * 0.001 for i in range(7)]
    lats = [12.968 + j * 0.001 for j in range(7)]
    nid = {}
    k = 0
    for i, lon in enumerate(lons):
        for j, lat in enumerate(lats):
            graph.add_node(k, x=lon, y=lat)
            nid[(i, j)] = k
            k += 1
    e = 0
    for i in range(7):
        for j in range(7):
            if i + 1 < 7:
                graph.add_edge(nid[(i, j)], nid[(i + 1, j)], osmid=e, length=110.0)
                e += 1
            if j + 1 < 7:
                graph.add_edge(nid[(i, j)], nid[(i, j + 1)], osmid=e, length=110.0)
                e += 1
    ox.save_graphml(graph, path)


def test_compute_end_to_end_offline(tmp_path):
    # Full pipeline on the synthetic GTFS + boundary with a local walk graph (no Overpass).
    graphml = tmp_path / "walk.graphml"
    _dense_graphml(graphml)

    analysis = PTALAnalysis.from_files(
        gtfs=str(MINI),
        service_date="2024-01-03",
        boundary=str(BOUNDARY),
        profile="default",
        osm=str(graphml),
    )
    result = analysis.compute()

    assert {"poi_id", "ai", "ptal_band", "geometry"}.issubset(result.grid.columns)
    assert len(result.grid) > 0
    # at least one grid point should reach a stop (AI > 0)
    assert (result.grid["ai"] > 0).any()
    # manifest is populated for reproducibility
    assert result.manifest["profile"]["name"] == "default"
    assert result.manifest["summary"]["grid_points"] == len(result.grid)
