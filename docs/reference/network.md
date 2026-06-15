# Walk network (boundary, grid & routing)

These modules turn a study-area boundary into a **queryable pedestrian network**: load the
boundary, generate the POI grid, build the OSM walk graph, and wire grid centroids and GTFS
stops into one routable network. This is the network-distance basis the PTAL engine
(Phase 2) consumes.

They pull the geo stack (`osmnx`, `geopandas`, `pandana`, …), so they are imported from
their submodules rather than the lightweight top-level package.

## End-to-end example

```python
from ptal_gtfs import load_feeds, FeedSource
from ptal_gtfs.grid import load_boundary, make_grid
from ptal_gtfs.io.osm import build_walk_graph
from ptal_gtfs.network import build_walk_network, nearest_stops

# 1. Study area — a boundary file, a place name, or the GTFS hull
area = load_boundary("data/delhi_boundary.geojson")     # or "New Delhi, India"

# 2. POI grid over the area (metres; default 100 m spacing)
grid = make_grid(area, spacing_m=100)

# 3. OSM pedestrian network for the area (Overpass download; cached under cache/)
graph = build_walk_graph(area)                          # or source="walk.graphml"

# 4. GTFS stops, then one unified routable network with virtual connectors
stops = load_feeds([FeedSource("dtc", "data/dtc_gtfs.zip")], "2024-06-17").stops
walk = build_walk_network(graph, grid, stops, k_centroid=3, k_stop=1)

# 5. Nearest stops within an 8-minute bus walk (~640 m) for every grid point
sap = nearest_stops(walk, max_walk_m=640, max_n=5)      # poi_id, stop_id, walk_m, rank
```

## How it fits together

- **`StudyArea`** holds the boundary in WGS84 plus an auto-selected metric (UTM) CRS;
  all distances downstream are metres.
- **`make_grid`** lays a regular point lattice over the area and clips it to the polygon.
- **`build_walk_graph`** uses osmnx to build a routable graph (largest connected
  component, projected, edge `length` in metres). Reading `.osm.pbf` directly is out of
  scope for now (would need `pyrosm`); use Overpass or a saved GraphML.
- **`build_walk_network`** adds grid centroids and stops to the graph as extra nodes,
  joined to their *k* nearest network nodes by **virtual connector edges** (centroids → 3,
  stops → `k_stop`), and builds one `pandana.Network`.
- **`nearest_stops`** returns, for every grid point, the nearest `max_n` stops within
  `max_walk_m` network metres — tidy `(poi_id, stop_id, walk_m, rank)` rows.

## API — study area & grid

::: ptal_gtfs.grid
    options:
      show_root_heading: false
      members:
        - load_boundary
        - boundary_from_stops
        - make_grid
        - StudyArea

## API — OSM walk graph

::: ptal_gtfs.io.osm
    options:
      show_root_heading: false
      members:
        - build_walk_graph

## API — unified walk network

::: ptal_gtfs.network
    options:
      show_root_heading: false
      members:
        - build_walk_network
        - nearest_stops
        - WalkNetwork
