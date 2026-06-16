"""End-to-end PTAL over a city's full extent using the GTFS stops convex hull.

Like run_ptal_e2e.py, but the study area is ``boundary_from_stops(...)`` — the convex hull
of all the feed's stops, buffered — instead of a small central box. That covers the WHOLE
city, so the OSM download and grid are much larger and slower; the grid spacing defaults to
a coarser value to keep it tractable. Run from the tests/ directory:

    ../.venv/Scripts/python run_ptal_hull.py ahmedabad
    ../.venv/Scripts/python run_ptal_hull.py delhi india 250

City GTFS feeds live under tests/fixtures/<city>/ (git-ignored). Outputs go to results/.
"""

from __future__ import annotations

import os
import sys

import geopandas as gpd

from ptal_gtfs import FeedSource, compute_ptal, load_feeds, load_profile
from ptal_gtfs.grid import WGS84, boundary_from_stops, make_grid
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


def run_city(city, profile="india", spacing_m=250):
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

    # STUDY AREA = convex hull of all stops (buffered) -> the whole-city extent.
    area = boundary_from_stops(gtfs.stops, buffer_m=500)
    print(f"[{city}] hull area ~ {area.polygon_metric.area / 1e6:.1f} km^2")

    grid = make_grid(area, spacing_m=spacing_m)
    print(f"[{city}] grid points ({spacing_m:g} m spacing): {len(grid):,}")
    print(f"[{city}] downloading OSM for the whole hull (the slow part) ...", flush=True)
    graph = build_walk_graph(area)
    print(f"[{city}] walk graph {graph.number_of_nodes():,}n/{graph.number_of_edges():,}e")

    walk = build_walk_network(graph, grid, gtfs.stops, k_centroid=3, k_stop=3)
    thresholds = {m: ACCESS_M.get(m, 800) for m in set(freqs["mode"])}
    access = nearest_stops(
        walk, thresholds, max_n=50, stop_modes=freqs[["stop_id", "mode"]].drop_duplicates()
    )
    ptal = compute_ptal(access, freqs, profile=prof, all_poi_ids=grid["poi_id"].tolist())

    os.makedirs(RESULTS, exist_ok=True)
    scored = grid[["poi_id", "lon", "lat", "geometry"]].merge(ptal, on="poi_id", how="left")
    gdf = gpd.GeoDataFrame(scored, geometry="geometry", crs=area.crs_metric).to_crs(WGS84)
    gdf.to_file(f"{RESULTS}/{city}_hull_ptal_{profile}.gpkg", driver="GPKG")
    gdf.drop(columns="geometry").to_csv(f"{RESULTS}/{city}_hull_ptal_{profile}.csv", index=False)

    print(f"[{city}] === PTAL hull ({profile}) === band distribution:")
    print(ptal["ptal_band"].value_counts().sort_index().to_string())
    print(f"[{city}] AI total: {ptal['ai'].describe().round(2).to_dict()}")
    print(f"[{city}] saved -> {RESULTS}/{city}_hull_ptal_{profile}.csv + .gpkg")


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "ahmedabad"
    profile = sys.argv[2] if len(sys.argv) > 2 else "india"
    spacing = float(sys.argv[3]) if len(sys.argv) > 3 else 250
    run_city(city, profile, spacing)
    sys.stdout.flush()
    os._exit(0)  # pandana's native threads can hang interpreter shutdown
