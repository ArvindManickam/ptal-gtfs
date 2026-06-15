"""Tests for walk-graph extraction (``ptal_gtfs.io.osm``).

No live Overpass download is exercised; instead a tiny synthetic WGS84 graph is saved as
GraphML and loaded through the same code path used for real saved graphs.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import osmnx as ox

from ptal_gtfs.grid import load_boundary
from ptal_gtfs.io.osm import build_walk_graph

BOUNDARY = Path(__file__).parent / "fixtures" / "boundary.geojson"


def _synthetic_wgs_graph() -> nx.MultiDiGraph:
    """A 3-node connected chain plus a disconnected 2-node component, in WGS84."""
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    for node, (lon, lat) in {
        1: (77.589, 12.970),
        2: (77.590, 12.970),
        3: (77.591, 12.970),
    }.items():
        graph.add_node(node, x=lon, y=lat)
    graph.add_edge(1, 2, osmid=0, length=110.0)
    graph.add_edge(2, 3, osmid=1, length=110.0)
    # Disconnected component that must be dropped by largest_component.
    graph.add_node(9, x=77.5928, y=12.9730)
    graph.add_node(10, x=77.5930, y=12.9730)
    graph.add_edge(9, 10, osmid=2, length=20.0)
    return graph


def test_build_walk_graph_from_graphml(tmp_path):
    area = load_boundary(BOUNDARY)
    path = tmp_path / "mini_walk.graphml"
    ox.save_graphml(_synthetic_wgs_graph(), path)

    graph = build_walk_graph(area, source=path)

    # Largest component kept (the 3-node chain), the disconnected pair dropped.
    assert graph.number_of_nodes() == 3
    # Projected to a metric CRS: node coordinates are large UTM eastings, not lon/lat.
    assert max(d["x"] for _, d in graph.nodes(data=True)) > 1000
    # Every edge carries a positive length in metres.
    assert all(data["length"] > 0 for _, _, data in graph.edges(data=True))
