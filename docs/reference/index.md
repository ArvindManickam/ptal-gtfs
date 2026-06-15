# API reference

!!! note "Generated from the source"
    This reference is produced automatically from the package's
    [NumPy-style docstrings](https://numpydoc.readthedocs.io/en/latest/format.html)
    by [mkdocstrings](https://mkdocstrings.github.io/). It will fill in as the
    Phase 1 modules land.

## Implemented so far

- **[GTFS loading](gtfs.md)** (`ptal_gtfs.io.gtfs`) — load and merge one or more GTFS
  feeds, validate them, and build the peak-window frequency table:
  `FeedSource`, `load_feed`, `load_feeds`, `inspect`.

## Planned surface

The eventual top-level API is being built around a small surface (not yet implemented):

- `PTALAnalysis` — load inputs and run a computation.
- `PTALAnalysis.from_files(...)` — construct from GTFS / OSM / boundary paths.
- `PTALAnalysis.compute()` → `PTALResult`.
- `PTALResult.to_geopackage(...)`, `PTALResult.plot_map(...)` — outputs.
- `load_profile(...)` — load a configuration profile (`default`, `india`, …).

## Wiring up a module (once code exists)

When a module is implemented, document it by adding an mkdocstrings identifier to
a page — that single line renders the whole module's classes and functions:

```text
::: ptal_gtfs.core.ptal
```

At that point, also make the package importable during the docs build so
mkdocstrings can introspect it:

- **Locally:** `pip install -e ".[docs]"` before `mkdocs serve`.
- **On Read the Docs:** switch `.readthedocs.yaml` to install the package with its
  `docs` extra (see the commented block in that file).
