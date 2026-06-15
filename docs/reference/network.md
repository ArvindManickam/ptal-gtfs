# Walk network (boundary, grid & routing)

These modules turn a study-area boundary into a **queryable pedestrian network**: load the
boundary, generate the POI grid, build the OSM walk graph, and wire grid centroids and GTFS
stops into one routable network. This is the network-distance basis the PTAL engine
(Phase 2) consumes.

They pull the geo stack (`osmnx`, `geopandas`, `pandana`, …), so they are imported from
their submodules rather than the lightweight top-level package.

## With a boundary file

```python
from ptal_gtfs import load_feeds, FeedSource
from ptal_gtfs.grid import load_boundary, make_grid
from ptal_gtfs.io.osm import build_walk_graph
from ptal_gtfs.network import build_walk_network, nearest_stops

# 1. Study area — a boundary polygon file, or the GTFS hull (boundary_from_stops)
area = load_boundary("data/delhi_boundary.geojson")

# 2. POI grid over the area (metres; default 100 m spacing)
grid = make_grid(area, spacing_m=100)

# 3. OSM pedestrian network for the area (Overpass download; cached under cache/)
graph = build_walk_graph(area)                          # or source="walk.graphml"

# 4. GTFS feed(s), then one unified routable network with virtual connectors
gtfs = load_feeds([FeedSource("dtc", "data/dtc_gtfs.zip")], "2024-06-17")
walk = build_walk_network(graph, grid, gtfs.stops, k_centroid=3, k_stop=3)

# 5. Nearest stops per grid point, with a per-mode access distance
stop_modes = gtfs.frequencies[["stop_id", "mode"]].drop_duplicates()
sap = nearest_stops(walk, {"bus": 500, "metro": 2000}, max_n=50, stop_modes=stop_modes)
# -> poi_id, stop_id, walk_m, rank, mode   (a bare number also works: nearest_stops(walk, 640, 5))
```

## Without a boundary file (GTFS stops hull)

When you don't have a boundary file, derive the study area from the GTFS stops
themselves — their convex hull, buffered (default 500 m):

```python
from ptal_gtfs import load_feeds, FeedSource
from ptal_gtfs.grid import boundary_from_stops, make_grid
from ptal_gtfs.io.osm import build_walk_graph
from ptal_gtfs.network import build_walk_network, nearest_stops

# 1. Load GTFS first, then derive the study area from the stops
gtfs = load_feeds([FeedSource("dtc", "data/dtc_gtfs.zip")], "2024-06-17")
area = boundary_from_stops(gtfs.stops, buffer_m=500)

# 2-5. Same as the boundary-file flow
grid = make_grid(area, spacing_m=100)
graph = build_walk_graph(area)
walk = build_walk_network(graph, grid, gtfs.stops, k_centroid=3, k_stop=3)
stop_modes = gtfs.frequencies[["stop_id", "mode"]].drop_duplicates()
sap = nearest_stops(walk, {"bus": 500, "metro": 2000}, max_n=50, stop_modes=stop_modes)
```

The hull spans **all** stops, so for a city-wide feed this builds the walk network for
the whole service-area footprint — fine, just larger than a tight boundary.

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
- **`nearest_stops`** returns, for every grid point, the nearest `max_n` stops within the
  access distance — a single value, or a per-mode `{mode: metres}` mapping (e.g. bus
  500 m, metro 2000 m) given a `stop_modes` frame. Tidy
  `(poi_id, stop_id, walk_m, rank[, mode])` rows.

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
