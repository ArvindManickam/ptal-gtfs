"""Unified walk network: OSM graph + grid/stop connectors, routed with pandana.

The walking graph from :mod:`ptal_gtfs.io.osm` describes the street/footpath network.
To route from grid points to stops we add them to the graph as extra nodes joined by
**virtual connector edges** to their nearest network nodes (grid centroids to their
``k_centroid`` nearest, stops to their ``k_stop`` nearest), then build a single
``pandana.Network``. ``nearest_stops`` then answers "the nearest N stops within a walk
distance for every grid point" — the network-distance basis for the Phase 2 SAP step.

All coordinates are in the study area's metric CRS and all weights are metres.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import numpy as np
import pandana
import pandas as pd
from scipy.spatial import cKDTree

WGS84 = "EPSG:4326"


@dataclass
class WalkNetwork:
    """A pandana walk network with grid-centroid and stop nodes wired in."""

    net: pandana.Network
    crs_metric: object
    stop_nodes: pd.DataFrame  # stop_id, node_id, x, y
    centroid_nodes: pd.DataFrame  # poi_id, node_id


def _graph_to_frames(graph: nx.MultiDiGraph) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Node (id -> x, y) and edge (from, to, weight=length) frames from a walk graph."""
    nodes = pd.DataFrame.from_dict(
        {n: (d["x"], d["y"]) for n, d in graph.nodes(data=True)},
        orient="index",
        columns=["x", "y"],
    )
    nodes.index.name = "node_id"
    edge_list = nx.to_pandas_edgelist(graph)
    edges = edge_list[["source", "target", "length"]].rename(
        columns={"source": "from", "target": "to", "length": "weight"}
    )
    return nodes, edges


def _connectors(
    tree: cKDTree,
    osm_ids: np.ndarray,
    points_xy: np.ndarray,
    point_ids: np.ndarray,
    k: int,
    factor: float,
) -> pd.DataFrame:
    """Connector edges from each point to its ``k`` nearest OSM nodes (Euclidean metres)."""
    k = min(k, len(osm_ids))
    dist, idx = tree.query(points_xy, k=k)
    if k == 1:
        dist, idx = dist[:, None], idx[:, None]
    return pd.DataFrame(
        {
            "from": np.repeat(point_ids, k),
            "to": osm_ids[idx.ravel()],
            "weight": dist.ravel().astype(float) * factor,
        }
    )


def build_walk_network(
    graph: nx.MultiDiGraph,
    centroids: gpd.GeoDataFrame,
    stops: pd.DataFrame,
    *,
    k_centroid: int = 3,
    k_stop: int = 1,
    connector_factor: float = 1.0,
) -> WalkNetwork:
    """Build a pandana walk network from an OSM graph plus centroid/stop connectors.

    Parameters
    ----------
    graph:
        A projected walk graph (from :func:`ptal_gtfs.io.osm.build_walk_graph`).
    centroids:
        Grid points with ``poi_id`` and metric ``x``/``y`` (from
        :func:`ptal_gtfs.grid.make_grid`), in the same metric CRS as ``graph``.
    stops:
        GTFS stops with ``stop_id``/``stop_lon``/``stop_lat`` (from ``load_feeds``).
    k_centroid, k_stop:
        Number of nearest network nodes each centroid/stop connects to.
    connector_factor:
        Multiplier on connector lengths (1.0 = straight-line metres).
    """
    crs_metric = graph.graph.get("crs")
    nodes, edges = _graph_to_frames(graph)

    osm_ids = nodes.index.to_numpy()
    tree = cKDTree(nodes[["x", "y"]].to_numpy())

    centroid_xy = centroids[["x", "y"]].to_numpy()
    stop_pts = gpd.GeoSeries(
        gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]), crs=WGS84
    ).to_crs(crs_metric)
    stop_xy = np.c_[stop_pts.x.to_numpy(), stop_pts.y.to_numpy()]

    # Fresh integer ids for the added nodes, above the OSM id range so they never collide.
    base = int(osm_ids.max()) + 1
    centroid_ids = np.arange(base, base + len(centroid_xy), dtype="int64")
    base2 = int(centroid_ids.max()) + 1 if len(centroid_ids) else base
    stop_ids = np.arange(base2, base2 + len(stop_xy), dtype="int64")

    centroid_conn = _connectors(
        tree, osm_ids, centroid_xy, centroid_ids, k_centroid, connector_factor
    )
    stop_conn = _connectors(tree, osm_ids, stop_xy, stop_ids, k_stop, connector_factor)

    extra_nodes = pd.DataFrame(
        {
            "x": np.r_[centroid_xy[:, 0], stop_xy[:, 0]],
            "y": np.r_[centroid_xy[:, 1], stop_xy[:, 1]],
        },
        index=np.r_[centroid_ids, stop_ids],
    )
    extra_nodes.index.name = "node_id"
    all_nodes = pd.concat([nodes, extra_nodes])
    all_edges = pd.concat([edges, centroid_conn, stop_conn], ignore_index=True)

    net = pandana.Network(
        all_nodes["x"],
        all_nodes["y"],
        all_edges["from"],
        all_edges["to"],
        all_edges[["weight"]],
        twoway=True,
    )

    return WalkNetwork(
        net=net,
        crs_metric=crs_metric,
        stop_nodes=pd.DataFrame(
            {
                "stop_id": stops["stop_id"].to_numpy(),
                "node_id": stop_ids,
                "x": stop_xy[:, 0],
                "y": stop_xy[:, 1],
            }
        ),
        centroid_nodes=pd.DataFrame(
            {"poi_id": centroids["poi_id"].to_numpy(), "node_id": centroid_ids}
        ),
    )


def nearest_stops(walk_network: WalkNetwork, max_walk_m: float, max_n: int) -> pd.DataFrame:
    """Nearest ``max_n`` stops within ``max_walk_m`` (network metres) of each grid point.

    Returns
    -------
    pandas.DataFrame
        Tidy ``poi_id``, ``stop_id``, ``walk_m``, ``rank`` rows; only reachable stops.
    """
    net = walk_network.net
    net.precompute(max_walk_m)

    poi_x = pd.Series(
        walk_network.stop_nodes["x"].to_numpy(), index=walk_network.stop_nodes["stop_id"]
    )
    poi_y = pd.Series(
        walk_network.stop_nodes["y"].to_numpy(), index=walk_network.stop_nodes["stop_id"]
    )
    net.set_pois("stops", max_walk_m, max_n, poi_x, poi_y)

    res = net.nearest_pois(max_walk_m, "stops", num_pois=max_n, include_poi_ids=True)

    centroids = walk_network.centroid_nodes
    sub = res.reindex(centroids["node_id"].to_numpy())
    node_to_poi = pd.Series(centroids["poi_id"].to_numpy(), index=centroids["node_id"].to_numpy())

    frames = []
    for rank in range(1, max_n + 1):
        frames.append(
            pd.DataFrame(
                {
                    "poi_id": node_to_poi.loc[sub.index].to_numpy(),
                    "stop_id": sub[f"poi{rank}"].to_numpy(),
                    "walk_m": sub[rank].to_numpy(),
                    "rank": rank,
                }
            )
        )
    out = pd.concat(frames, ignore_index=True)
    out = out[out["walk_m"] < max_walk_m].dropna(subset=["stop_id"])
    return out.reset_index(drop=True)
