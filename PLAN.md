# Project Plan — gtfs-ptal

Goal: a scalable, well-documented Python package (library + CLI) that computes PTAL
for Indian cities from GTFS + OSM, faithful to the TfL method by default and
explicitly parameterised for Indian conditions.

Guiding principles:

1. **Methodology first.** `docs/methodology.md` is the source of truth; code
   implements it, tests verify it. Any methodological change is made in the doc
   before the code.
2. **Reproduce TfL before adapting it.** The `default` profile must reproduce the
   published TfL calculation on a worked example before the `india` profile exists.
3. **Everything configurable, nothing hard-coded.** Parameters live in config
   profiles, not in function bodies.
4. **Scale by design.** Vectorised operations and contraction-hierarchy shortest
   paths from day one; no per-grid-point Python loops in the hot path.
5. **Docs and tests move with code.** Each phase ends with its docs and tests done,
   not deferred.

---

## Phase 0 — Project setup ✅ (this commit)

- [x] Git repository, GPL-3.0 license, `.gitignore`
- [x] README, PLAN, methodology / architecture / data docs
- [x] CLAUDE.md / AGENTS.md agent guidance
- [x] `pyproject.toml` skeleton (src layout, dependencies declared)
- [ ] Set up remote (GitHub), CI placeholder (ruff + pytest via GitHub Actions)

## Phase 1 — Data layer

Load and validate the three inputs into clean internal representations.

- [ ] **GTFS reader** (`gtfs_ptal.io.gtfs`): load feed(s), validate required
      files/fields, filter to service date + peak window, compute per-route
      per-stop scheduled headways. Support multiple feeds per city (e.g. bus + metro).
- [ ] **OSM walking network** (`gtfs_ptal.io.osm`): build a pedestrian network from
      a `.osm.pbf` extract or Overpass download; clean/simplify; export to a fast
      routing structure (pandana).
- [ ] **Study area & grid** (`gtfs_ptal.grid`): generate the point grid (default
      100 m, configurable) from a boundary polygon, GTFS extent, or named place.
- [ ] Integrate any existing user scripts that cover these steps.
- [ ] Tests with a small fixture feed + OSM clip.

**Milestone M1:** `gtfs-ptal inspect` CLI command prints a validated summary of a
real Indian GTFS feed (routes, stops, modes, peak frequencies).

## Phase 2 — PTAL engine (TfL-faithful)

- [ ] **Service Access Points** (`core.sap`): nearest network walk distance from
      each grid point to each stop within the mode's max access time; de-duplicate
      routes (keep best SAP per route).
- [ ] **Waiting time** (`core.awt`): SWT = 0.5 × (60 / frequency); AWT = SWT +
      reliability factor.
- [ ] **EDF & Accessibility Index** (`core.ptal`): TAT = walk + AWT;
      EDF = 30 / TAT; AI per mode = max EDF + 0.5 × Σ(other EDFs); total AI = Σ modes;
      band mapping (0, 1a–6b).
- [ ] Golden-number test: reproduce TfL's published worked example exactly.

**Milestone M2:** end-to-end PTAL grid for one Indian city using the `default`
(TfL) profile, validated against a hand calculation for sample points.

## Phase 3 — Indian adaptations

- [ ] **Config profiles** (`gtfs_ptal.config`): pydantic-validated YAML profiles;
      ship `default` (TfL) and `india`; users can derive city profiles.
- [ ] **IPT layer** (`io.ipt`): ingest informal services from CSV/GeoJSON
      (stops or corridors + headways) as an additional mode.
- [ ] **Walkability adjustments**: per-mode walk speeds and max access times;
      optional crossing-penalty model using OSM road class data.
- [ ] **Reliability recalibration**: per-mode reliability factors; optional
      headway-irregularity adjustment when AVL/observed data is available.
- [ ] Document every adapted parameter and its justification in
      `docs/methodology.md` (this is the research contribution).

**Milestone M3:** side-by-side `default` vs `india` profile comparison for one
city, with a written analysis of the differences.

## Phase 4 — Outputs & visualisation

- [ ] Export: GeoPackage, GeoParquet, GeoTIFF (rasterised bands), CSV.
- [ ] Interactive HTML map (folium/lonboard) with the standard PTAL colour scheme.
- [ ] Aggregations: ward/zone-level statistics, population-weighted PTAL when a
      population raster is supplied.
- [ ] Summary report (HTML/Markdown) per run.

## Phase 5 — CLI & user experience

- [ ] `gtfs-ptal compute | inspect | profile | map` commands (typer).
- [ ] Run manifest: every output directory gets a `run.yaml` recording inputs,
      profile, parameters, package version — full reproducibility.
- [ ] Helpful errors for the common GTFS quality problems in Indian feeds.

## Phase 6 — Scalability & quality

- [ ] Benchmark on a metro-scale city (Delhi/Bengaluru-sized: ~10⁵–10⁶ grid points).
- [ ] Chunked/parallel grid processing; optional dask backend if needed.
- [ ] Memory profile; cache the walking network between runs.
- [ ] Test coverage target ≥ 85% on `core/`; property-based tests for band edges.

## Phase 7 — Release & case study

- [ ] Full API docs (mkdocs-material), tutorial notebook for one Indian city.
- [ ] `CITATION.cff`, Zenodo DOI.
- [ ] Publish to PyPI; announce.

---

## Open methodology decisions (to resolve before/while Phase 3)

Tracked here so they don't get lost; each gets resolved in `docs/methodology.md`.

| # | Decision | Options / notes |
| --- | --- | --- |
| D1 | India-profile walking speed | TfL uses 80 m/min (4.8 km/h). Indian literature suggests lower effective speeds; pick default + cite. |
| D2 | Max access time per mode | TfL: 8 min bus, 12 min rail. What for metro, BRT, IPT? |
| D3 | IPT reliability factor & frequency source | Observed headways vs assumed; how to treat route ambiguity of shared autos. |
| D4 | Peak window per city | TfL uses 08:15–09:15. Configurable per profile; what default for India? |
| D5 | Band thresholds | Keep TfL bands for comparability, or recalibrate to Indian AI distributions? Recommend: keep TfL bands, report raw AI alongside. |
| D6 | Crossing/barrier penalties | Off by default? Simple per-crossing time penalty vs detailed model. |
