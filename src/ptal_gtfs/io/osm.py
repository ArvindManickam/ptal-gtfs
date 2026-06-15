"""OSM pedestrian network extraction.

Builds a routable walking graph for a study area with osmnx, either downloaded from
the Overpass API or loaded from a saved GraphML file (the offline/reproducible path used
in tests). The graph is reduced to its largest connected component and projected to the
area's metric CRS, with edge ``length`` in metres (methodology §2).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox

from ..grid import StudyArea

CACHE_DIR = Path("cache")


def build_walk_graph(
    area: StudyArea,
    *,
    source: str | Path = "overpass",
    simplify: bool = True,
    cache: bool = True,
) -> nx.MultiDiGraph:
    """Build a projected, largest-component pedestrian graph for ``area``.

    Parameters
    ----------
    area:
        The study area; the graph is projected to ``area.crs_metric``.
    source:
        ``"overpass"`` to download the walk network from OpenStreetMap, or a path to a
        saved GraphML file to load instead (offline/reproducible).
    simplify:
        Whether osmnx should simplify the graph topology (Overpass path only).
    cache:
        Cache the downloaded (unprojected) graph as GraphML under ``cache/`` and reuse it.

    Notes
    -----
    Reading ``.osm.pbf`` directly is out of scope here (would need ``pyrosm``); use a
    GraphML export or the Overpass path for now.
    """
    if str(source) != "overpass":
        graph = ox.load_graphml(source)
    else:
        graph = _download_walk_graph(area, simplify=simplify, cache=cache)

    graph = ox.truncate.largest_component(graph, strongly=False)
    graph = ox.project_graph(graph, to_crs=area.crs_metric)
    _ensure_edge_lengths(graph)
    return graph


def _download_walk_graph(area: StudyArea, *, simplify: bool, cache: bool) -> nx.MultiDiGraph:
    cache_path = CACHE_DIR / f"walk_{_cache_key(area, simplify)}.graphml"
    if cache and cache_path.exists():
        return ox.load_graphml(cache_path)

    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    graph = ox.graph_from_polygon(area.polygon, network_type="walk", simplify=simplify)
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(graph, cache_path)
    return graph


def _cache_key(area: StudyArea, simplify: bool) -> str:
    signature = f"{area.polygon.bounds}-walk-{simplify}"
    return hashlib.md5(signature.encode()).hexdigest()[:12]


def _ensure_edge_lengths(graph: nx.MultiDiGraph) -> None:
    """Fill any missing edge ``length`` from projected node coordinates (metres)."""
    for u, v, data in graph.edges(data=True):
        if "length" not in data or data["length"] is None:
            ux, uy = graph.nodes[u]["x"], graph.nodes[u]["y"]
            vx, vy = graph.nodes[v]["x"], graph.nodes[v]["y"]
            data["length"] = float(np.hypot(ux - vx, uy - vy))
