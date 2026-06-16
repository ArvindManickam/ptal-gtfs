"""Top-level PTAL workflow: :class:`PTALAnalysis` -> :class:`PTALResult`.

This is the product's main entry point. ``PTALAnalysis.from_files(...)`` takes the inputs
(GTFS feeds, a service date, an optional boundary file, and a profile), ``.compute()`` runs
the whole pipeline (load feeds → study area → grid → OSM walk network → per-mode SAP →
PTAL), and the returned :class:`PTALResult` writes the outputs (GeoPackage, CSV, an
interactive HTML map, and a ``run.yaml`` reproducibility manifest).

The OSM walk network is downloaded automatically (Overpass), so no OSM file is required;
a saved GraphML may be passed for offline/reproducible runs. Everything else about the
method (peak window, grid spacing, access thresholds, walk speed, reliability, bands) comes
from the **profile** (the config file).
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import yaml

from . import __version__
from .config import Profile, load_profile
from .grid import WGS84, boundary_from_stops, load_boundary, make_grid
from .io.gtfs import FeedSource, load_feeds
from .io.osm import build_walk_graph
from .network import build_walk_network, nearest_stops
from .ptal import compute_ptal

# PTAL band -> colour (low access light/blue, high access warm/red), for plot_map.
PTAL_COLORS = {
    "0": "#bdbdbd",
    "1a": "#2c7fb8",
    "1b": "#41b6c4",
    "2": "#7fcdbb",
    "3": "#c7e9b4",
    "4": "#ffffb2",
    "5": "#fecc5c",
    "6a": "#fd8d3c",
    "6b": "#e31a1c",
}

_DEFAULT_ACCESS_M = 800.0
_MAX_STOPS_PER_POI = 50


def _to_feeds(gtfs) -> list[FeedSource]:
    """Normalise the ``gtfs`` argument to a list of :class:`FeedSource`."""
    if isinstance(gtfs, FeedSource):
        return [gtfs]
    if isinstance(gtfs, (str, Path)):
        return [FeedSource(Path(gtfs).stem, gtfs)]
    if isinstance(gtfs, dict):
        return [FeedSource(key, path) for key, path in gtfs.items()]
    return [g if isinstance(g, FeedSource) else FeedSource(Path(g).stem, g) for g in gtfs]


@dataclass
class PTALResult:
    """The scored PTAL grid plus a reproducibility manifest, with output writers."""

    grid: gpd.GeoDataFrame  # cells, ai_<mode>, ai, ptal_band (WGS84)
    manifest: dict

    @property
    def bands(self):
        """Count of grid points per PTAL band."""
        return self.grid["ptal_band"].value_counts().sort_index()

    def to_geopackage(self, path: str | Path) -> Path:
        """Write the scored grid to a GeoPackage (for QGIS)."""
        self.grid.to_file(path, driver="GPKG")
        return Path(path)

    def to_csv(self, path: str | Path) -> Path:
        """Write the scored grid (without geometry) to CSV."""
        self.grid.drop(columns="geometry").to_csv(path, index=False)
        return Path(path)

    def to_manifest(self, path: str | Path) -> Path:
        """Write the run.yaml reproducibility manifest."""
        Path(path).write_text(yaml.safe_dump(self.manifest, sort_keys=False), encoding="utf-8")
        return Path(path)

    def plot_map(self, path: str | Path, *, tiles: str = "cartodbpositron") -> Path:
        """Write an interactive HTML map of the PTAL grid (cells shaded by band)."""
        import folium

        min_x, min_y, max_x, max_y = self.grid.total_bounds
        fmap = folium.Map(
            location=[(min_y + max_y) / 2, (min_x + max_x) / 2], zoom_start=12, tiles=tiles
        )
        folium.GeoJson(
            self.grid.to_json(),
            style_function=lambda feat: {
                "fillColor": PTAL_COLORS.get(feat["properties"]["ptal_band"], "#000000"),
                "color": None,
                "weight": 0,
                "fillOpacity": 0.6,
            },
            tooltip=folium.GeoJsonTooltip(fields=["poi_id", "ai", "ptal_band"]),
        ).add_to(fmap)
        fmap.save(str(path))
        return Path(path)

    def save(self, prefix: str | Path) -> Path:
        """Write the GeoPackage, CSV and run.yaml manifest using a common path prefix."""
        prefix = Path(prefix)
        self.to_geopackage(prefix.with_name(prefix.name + ".gpkg"))
        self.to_csv(prefix.with_name(prefix.name + ".csv"))
        self.to_manifest(prefix.with_name(prefix.name + "_run.yaml"))
        return prefix


@dataclass
class PTALAnalysis:
    """A configured PTAL run. Build with :meth:`from_files`, then call :meth:`compute`."""

    feeds: list[FeedSource]
    service_date: _dt.date | str
    profile: Profile
    boundary: str | Path | None = None
    osm: str | Path = "overpass"
    k_centroid: int = 3
    k_stop: int = 3
    verbose: bool = False

    @classmethod
    def from_files(
        cls,
        gtfs,
        service_date: _dt.date | str,
        *,
        boundary: str | Path | None = None,
        profile: str | Path | Profile = "default",
        osm: str | Path = "overpass",
        k_centroid: int = 3,
        k_stop: int = 3,
        verbose: bool = False,
    ) -> PTALAnalysis:
        """Configure a run from files.

        Parameters
        ----------
        gtfs:
            A GTFS zip path, a list of paths, a ``{key: path}`` mapping, or
            :class:`FeedSource`/list thereof (one feed per operator).
        service_date:
            Calendar date to score (``datetime.date`` or ``"YYYY-MM-DD"``).
        boundary:
            Path to a study-area polygon file; ``None`` uses the GTFS stops hull.
        profile:
            Config profile: a shipped name (``"default"``, ``"india"``), a YAML path, or a
            :class:`~ptal_gtfs.config.Profile`.
        osm:
            ``"overpass"`` to download the walk network (default), or a saved GraphML path.
        """
        prof = profile if isinstance(profile, Profile) else load_profile(profile)
        return cls(
            feeds=_to_feeds(gtfs),
            service_date=service_date,
            profile=prof,
            boundary=boundary,
            osm=osm,
            k_centroid=k_centroid,
            k_stop=k_stop,
            verbose=verbose,
        )

    def compute(self, *, verbose: bool | None = None) -> PTALResult:
        """Run the full pipeline and return the scored grid + manifest.

        Pass ``verbose=True`` (or set it on the analysis) to print each step with the
        elapsed time — useful for seeing where a long run is spending its time (usually the
        OSM download or, at whole-city scale, the grid size).
        """
        verbose = self.verbose if verbose is None else verbose
        start = time.time()

        def step(msg: str) -> None:
            if verbose:
                print(f"[ptal {time.time() - start:6.1f}s] {msg}", flush=True)

        prof = self.profile
        step("loading GTFS feeds + peak frequencies ...")
        gtfs = load_feeds(
            self.feeds,
            self.service_date,
            peak_start=prof.peak_window.start,
            peak_end=prof.peak_window.end,
        )
        step(f"  {len(gtfs.stops):,} stops, {len(gtfs.routes):,} routes")

        known = set(prof.reliability.by_mode)
        freqs = gtfs.frequencies[gtfs.frequencies["mode"].isin(known)]
        if freqs.empty:
            raise ValueError(
                "no scorable modes — check the service_date is within the feed calendar "
                "and that route_type maps to a mode the profile covers"
            )

        if self.boundary:
            step(f"loading boundary {self.boundary} ...")
            area = load_boundary(self.boundary)
        else:
            step("building study area from the GTFS stops hull ...")
            area = boundary_from_stops(gtfs.stops)
        step(f"  study area ~ {area.polygon_metric.area / 1e6:,.0f} km^2")

        step(f"generating {prof.grid.spacing_m:g} m cell grid ...")
        grid = make_grid(area, spacing_m=prof.grid.spacing_m, cell=True)
        step(f"  {len(grid):,} grid cells")

        if str(self.osm) == "overpass":
            step("downloading OSM walk network from Overpass (usually the slow part) ...")
        else:
            step(f"loading OSM walk graph {self.osm} ...")
        graph = build_walk_graph(area, source=self.osm)
        step(f"  walk graph {graph.number_of_nodes():,} nodes / {graph.number_of_edges():,} edges")

        thresholds = {m: prof.access_m.get(m, _DEFAULT_ACCESS_M) for m in set(freqs["mode"])}
        stop_pts = gpd.GeoSeries(
            gpd.points_from_xy(gtfs.stops["stop_lon"], gtfs.stops["stop_lat"]), crs=WGS84
        ).to_crs(area.crs_metric)
        near = stop_pts.within(area.polygon_metric.buffer(max(thresholds.values()))).to_numpy()
        stops_near = gtfs.stops[near]

        step(f"building walk network ({len(stops_near):,} stops + connectors) ...")
        walk = build_walk_network(
            graph, grid, stops_near, k_centroid=self.k_centroid, k_stop=self.k_stop
        )
        step("computing nearest stops per grid point (SAP) ...")
        access = nearest_stops(
            walk,
            thresholds,
            max_n=_MAX_STOPS_PER_POI,
            stop_modes=freqs[["stop_id", "mode"]].drop_duplicates(),
        )
        step(f"  {len(access):,} (poi, stop) access pairs")

        step("computing PTAL (WT -> SWT -> AWT -> EDF -> AI -> band) ...")
        ptal = compute_ptal(access, freqs, profile=prof, all_poi_ids=grid["poi_id"].tolist())

        scored = grid[["poi_id", "lon", "lat", "geometry"]].merge(ptal, on="poi_id", how="left")
        gdf = gpd.GeoDataFrame(scored, geometry="geometry", crs=area.crs_metric).to_crs(WGS84)
        step(f"done in {time.time() - start:.1f}s - {len(gdf):,} cells scored")
        return PTALResult(grid=gdf, manifest=self._manifest(area, graph, gdf))

    def run(self, output: str | Path, *, plot: bool = False, verbose: bool | None = None) -> None:
        """Compute, write outputs, print the band summary, then **exit the process**.

        A convenience for command-line scripts. It ends with ``os._exit(0)`` to sidestep
        the interpreter-shutdown hang that pandana's native threads can cause on Windows.
        Because it terminates the process, do **not** call it from a notebook, a web app,
        or any program that must keep running — use :meth:`compute` and the
        :class:`PTALResult` methods directly there instead.

        Parameters
        ----------
        output:
            Path prefix for the outputs (writes ``<output>.gpkg``, ``.csv`` and
            ``_run.yaml``; also ``<output>.html`` when ``plot=True``).
        plot:
            Also write the interactive HTML map (avoid at whole-city scale).
        verbose:
            Print step-by-step progress (defaults to the analysis's ``verbose``).
        """
        result = self.compute(verbose=verbose)
        result.save(output)
        if plot:
            result.plot_map(f"{output}.html")
        print(result.bands)
        sys.stdout.flush()
        os._exit(0)

    def _manifest(self, area, graph, gdf) -> dict:
        bands = {str(k): int(v) for k, v in gdf["ptal_band"].value_counts().sort_index().items()}
        return {
            "ptal_gtfs_version": __version__,
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "inputs": {
                "feeds": [{"key": f.key, "path": str(f.path)} for f in self.feeds],
                "service_date": str(self.service_date),
                "boundary": str(self.boundary) if self.boundary else "gtfs_hull",
                "osm": str(self.osm),
            },
            "profile": self.profile.model_dump(),
            "summary": {
                "grid_points": int(len(gdf)),
                "walk_graph": {
                    "nodes": graph.number_of_nodes(),
                    "edges": graph.number_of_edges(),
                },
                "bands": bands,
            },
        }
