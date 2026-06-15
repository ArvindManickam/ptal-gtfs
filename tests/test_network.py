"""Tests for the unified walk network and connectors (``ptal_gtfs.network``)."""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer
from scipy.spatial import cKDTree

from ptal_gtfs.network import _connectors, build_walk_network, nearest_stops


def _line_graph(max_x: int, step: int = 100) -> nx.MultiDiGraph:
    """A straight chain of nodes from x=0 to x=max_x, ``step`` metres apart."""
    graph = nx.MultiDiGraph(crs="EPSG:32643")
    xs = list(range(0, max_x + step, step))
    for i, x in enumerate(xs):
        graph.add_node(i, x=float(x), y=0.0)
    for i in range(len(xs) - 1):
        graph.add_edge(i, i + 1, length=float(step))
    return graph


def _chain_graph() -> nx.MultiDiGraph:
    """Three nodes in a line, 100 m apart, in a metric CRS: 1(0,0)-2(100,0)-3(200,0)."""
    graph = nx.MultiDiGraph(crs="EPSG:32643")
    graph.add_node(1, x=0.0, y=0.0)
    graph.add_node(2, x=100.0, y=0.0)
    graph.add_node(3, x=200.0, y=0.0)
    graph.add_edge(1, 2, length=100.0)
    graph.add_edge(2, 3, length=100.0)
    return graph


def _stops_at(coords_xy):
    """Build a stops frame whose lon/lat reproject to the given metric coordinates."""
    to_wgs = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lons, lats = zip(*(to_wgs.transform(x, y) for x, y in coords_xy), strict=True)
    return pd.DataFrame(
        {"stop_id": [f"S{i}" for i in range(len(coords_xy))], "stop_lon": lons, "stop_lat": lats}
    )


def test_connectors_join_k_nearest_nodes():
    osm_ids = np.array([1, 2, 3])
    tree = cKDTree(np.array([[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]]))
    conn = _connectors(tree, osm_ids, np.array([[0.0, 5.0]]), np.array([10]), k=3, factor=1.0)
    assert len(conn) == 3
    assert set(conn["to"]) == {1, 2, 3}
    # Nearest node (1) is 5 m away.
    assert conn["weight"].min() == 5.0


def test_nearest_stops_distance_matches_hand_calc():
    # Centroid at (0,5) -> node1 (5 m); stop at (200,5) -> node3 (5 m).
    # Path: 5 + 100 + 100 + 5 = 210 m.
    centroids = pd.DataFrame({"poi_id": [0], "x": [0.0], "y": [5.0]})
    stops = _stops_at([(200.0, 5.0)])
    wn = build_walk_network(_chain_graph(), centroids, stops, k_centroid=1, k_stop=1)

    res = nearest_stops(wn, max_walk_m=300, max_n=1)
    assert len(res) == 1
    assert res.iloc[0]["stop_id"] == "S0"
    assert abs(res.iloc[0]["walk_m"] - 210.0) < 1.0


def test_nearest_stops_respects_max_walk_distance():
    centroids = pd.DataFrame({"poi_id": [0], "x": [0.0], "y": [5.0]})
    stops = _stops_at([(200.0, 5.0)])
    wn = build_walk_network(_chain_graph(), centroids, stops, k_centroid=1, k_stop=1)
    # 210 m path is beyond a 150 m limit -> no stop returned.
    assert nearest_stops(wn, max_walk_m=150, max_n=1).empty


def _two_mode_setup():
    """A bus stop ~310 m and a metro stop ~1510 m from a centroid at (0, 5)."""
    graph = _line_graph(1500)
    centroids = pd.DataFrame({"poi_id": [0], "x": [0.0], "y": [5.0]})
    stops = _stops_at([(300.0, 5.0), (1500.0, 5.0)])
    stops["stop_id"] = ["bus_stop", "metro_stop"]
    wn = build_walk_network(graph, centroids, stops, k_centroid=1, k_stop=1)
    stop_modes = pd.DataFrame({"stop_id": ["bus_stop", "metro_stop"], "mode": ["bus", "metro"]})
    return wn, stop_modes


def test_nearest_stops_per_mode_applies_per_mode_distance():
    wn, stop_modes = _two_mode_setup()
    res = nearest_stops(wn, {"bus": 500, "metro": 2000}, max_n=5, stop_modes=stop_modes)

    got = dict(zip(res["stop_id"], res["mode"], strict=True))
    assert got == {"bus_stop": "bus", "metro_stop": "metro"}
    bus_d = res.loc[res.stop_id == "bus_stop", "walk_m"].iloc[0]
    metro_d = res.loc[res.stop_id == "metro_stop", "walk_m"].iloc[0]
    assert abs(bus_d - 310) < 1.0
    assert abs(metro_d - 1510) < 1.0


def test_nearest_stops_per_mode_drops_stop_beyond_its_threshold():
    wn, stop_modes = _two_mode_setup()
    # Metro path (~1510 m) exceeds a 1000 m metro limit -> only the bus stop remains.
    res = nearest_stops(wn, {"bus": 500, "metro": 1000}, max_n=5, stop_modes=stop_modes)
    assert set(res["stop_id"]) == {"bus_stop"}


def test_nearest_stops_per_mode_requires_stop_modes():
    wn, _ = _two_mode_setup()
    with pytest.raises(ValueError):
        nearest_stops(wn, {"bus": 500}, max_n=5)
