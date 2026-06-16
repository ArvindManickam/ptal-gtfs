"""End-to-end PTAL pipeline over the GTFS fixture cities.

Standalone runner (not a pytest test — it downloads the OSM walk network and is slow).
Run from the repo root:

    .venv/Scripts/python tests/run_ptal_e2e.py delhi
    .venv/Scripts/python tests/run_ptal_e2e.py hyderabad india
    .venv/Scripts/python tests/run_ptal_e2e.py ahmedabad default

City GTFS feeds live under tests/fixtures/<city>/ (git-ignored). Outputs go to results/.
"""

from __future__ import annotations

import json
import math
import os
import sys

import geopandas as gpd

from ptal_gtfs import FeedSource, compute_ptal, load_feeds, load_profile
from ptal_gtfs.grid import WGS84, load_boundary, make_grid
from ptal_gtfs.io.osm import build_walk_graph
from ptal_gtfs.network import build_walk_network, nearest_stops

FIXTURES = "fixtures"
RESULTS = "results"

CITIES = {
    "delhi": {
        "feeds": {"dtc": "delhi/DTC_GTFS-bus.zip", "dmrc": "delhi/DMRC_GTFS-metros.zip"},
        "service_date": "2024-06-17",
    },
    "hyderabad": {
        "feeds": {"tgsrtc": "Hyderabad/TGSRTC.zip", "hmrl": "Hyderabad/HMRL.zip"},
        "service_date": "2026-06-17",  # Hyderabad feeds are dated 2026-2030
    },
    "ahmedabad": {
        "feeds": {"amts": "ahmd/Ahmedabad.zip"},
        "service_date": "2024-06-17",
    },
}

# Per-mode walk access thresholds (metres) for the SAP step.
ACCESS_M = {"bus": 500, "metro": 2000, "rail": 2000, "tram": 800}


def central_box(stops, size_m=10000):
    """A small square study area centred on the median stop (saved as a GeoJSON)."""
    lon0 = float(stops["stop_lon"].median())
    lat0 = float(stops["stop_lat"].median())
    d_lat = (size_m / 2) / 111_320.0
    d_lon = (size_m / 2) / (111_320.0 * math.cos(math.radians(lat0)))
    ring = [
        [lon0 - d_lon, lat0 - d_lat],
        [lon0 + d_lon, lat0 - d_lat],
        [lon0 + d_lon, lat0 + d_lat],
        [lon0 - d_lon, lat0 + d_lat],
        [lon0 - d_lon, lat0 - d_lat],
    ]
    os.makedirs(RESULTS, exist_ok=True)
    path = f"{RESULTS}/_boundary.geojson"
    with open(path, "w") as f:
        json.dump(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                    }
                ],
            },
            f,
        )
    return load_boundary(path)


def run_city(city, profile="india"):
    cfg = CITIES[city]
    prof = load_profile(profile)
    known = set(prof.reliability.by_mode)

    feeds = [FeedSource(key, f"{FIXTURES}/{rel}") for key, rel in cfg["feeds"].items()]
    gtfs = load_feeds(feeds, cfg["service_date"])
    print(f"[{city}] stops {len(gtfs.stops):,}  routes {len(gtfs.routes):,}")

    # Flag any GTFS route_type that did not map to a known mode (fell through to 'other').
    unmapped = gtfs.routes[gtfs.routes["mode"] == "other"]
    if len(unmapped):
        types = sorted(unmapped["route_type"].dropna().unique().tolist())
        n = len(unmapped)
        print(f"[{city}] !! UNMAPPED route_type {types}: {n} route(s) -> 'other', not scored")

    freqs = gtfs.frequencies[gtfs.frequencies["mode"].isin(known)]
    print(f"[{city}] modes scored: {sorted(set(freqs['mode']))}")
    if freqs.empty:
        print(f"[{city}] no scorable modes; nothing to compute.")
        return

    area = central_box(gtfs.stops)
    pts = gpd.GeoSeries(
        gpd.points_from_xy(gtfs.stops["stop_lon"], gtfs.stops["stop_lat"]), crs=WGS84
    ).to_crs(area.crs_metric)
    stops_near = gtfs.stops[
        pts.within(area.polygon_metric.buffer(max(ACCESS_M.values()))).to_numpy()
    ]
    stops_near = stops_near.reset_index(drop=True)

    grid = make_grid(area, spacing_m=100)
    graph = build_walk_graph(area)
    print(
        f"[{city}] stops near {len(stops_near):,}  grid {len(grid):,}  "
        f"walk graph {graph.number_of_nodes():,}n/{graph.number_of_edges():,}e"
    )

    walk = build_walk_network(graph, grid, stops_near, k_centroid=3, k_stop=3)
    thresholds = {m: ACCESS_M.get(m, 800) for m in set(freqs["mode"])}
    access = nearest_stops(
        walk, thresholds, max_n=50, stop_modes=freqs[["stop_id", "mode"]].drop_duplicates()
    )
    ptal = compute_ptal(access, freqs, profile=prof, all_poi_ids=grid["poi_id"].tolist())

    os.makedirs(RESULTS, exist_ok=True)
    scored = grid[["poi_id", "lon", "lat", "geometry"]].merge(ptal, on="poi_id", how="left")
    gdf = gpd.GeoDataFrame(scored, geometry="geometry", crs=area.crs_metric).to_crs(WGS84)
    gdf.to_file(f"{RESULTS}/{city}_ptal_{profile}.gpkg", driver="GPKG")
    gdf.drop(columns="geometry").to_csv(f"{RESULTS}/{city}_ptal_{profile}.csv", index=False)

    print(f"[{city}] === PTAL ({profile}) === band distribution:")
    print(ptal["ptal_band"].value_counts().sort_index().to_string())
    print(f"[{city}] AI total: {ptal['ai'].describe().round(2).to_dict()}")
    print(f"[{city}] saved -> {RESULTS}/{city}_ptal_{profile}.csv + .gpkg")


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "delhi"
    profile = sys.argv[2] if len(sys.argv) > 2 else "india"
    run_city(city, profile)
    sys.stdout.flush()
    os._exit(0)  # pandana's native threads can hang interpreter shutdown
