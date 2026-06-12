# Agent guidance — gtfs-ptal

Guidance for AI coding agents working in this repository. (CLAUDE.md imports this
file; keep all agent guidance here so every tool reads the same instructions.)

## What this project is

A Python package (library + CLI) computing Public Transport Accessibility Levels
(PTAL) for Indian cities from GTFS + OSM. TfL methodology, adapted for Indian
conditions. GPL-3.0. Pre-alpha: currently in docs-first planning.

## Ground rules

1. **`docs/methodology.md` is the source of truth.** Never change computation
   behaviour (formulas, parameters, thresholds) in code without updating the
   methodology doc in the same change. If code and doc disagree, the doc wins —
   flag the discrepancy, don't silently pick one.
2. **No magic numbers in code.** Every methodology parameter (walk speed,
   thresholds, reliability factors, band edges) comes from the config profile
   system (`gtfs_ptal/config/`). If you need a constant, it belongs in a profile.
3. **The `default` profile is sacred.** It must keep reproducing the TfL method
   exactly (golden tests in `tests/test_tfl_golden.py` once they exist). Indian
   adaptations go in the `india` profile or new code paths, never into `default`.
4. **Hot-path discipline.** Anything that runs per grid point must be vectorised
   (numpy/pandas/pandana). No Python loops over grid points or stops.
5. **Keep PLAN.md current.** When you complete a roadmap item, tick it. When scope
   changes, edit the plan in the same PR.
6. **Docs and tests are part of "done"**, not follow-ups.

## Repository conventions

- Layout: `src/` layout, package `gtfs_ptal`, tests in `tests/`, docs in `docs/`.
- Python ≥ 3.10. Type hints on public APIs. Docstrings: NumPy style.
- Lint/format: `ruff check` and `ruff format` (config in `pyproject.toml`).
- Tests: `pytest`. Fixture data lives in `tests/fixtures/` (tiny synthetic GTFS +
  OSM clip — never commit real city datasets).
- `data/` and `results/` are gitignored scratch space for real city data.
- Commits: imperative mood, scope prefix when natural (`gtfs:`, `core:`, `docs:`).

## Useful commands

```bash
pip install -e ".[dev]"     # dev install
pytest                      # run tests
ruff check . && ruff format --check .
```

## Domain glossary

| Term | Meaning |
| --- | --- |
| POI | Point of Interest — a grid point where PTAL is computed |
| SAP | Service Access Point — a stop/station reachable from a POI |
| SWT / AWT | Scheduled / Average Waiting Time |
| TAT | Total Access Time = walk time + AWT |
| EDF | Equivalent Doorstep Frequency = 30 / TAT |
| AI | Accessibility Index (per mode, then summed) |
| IPT | Intermediate Public Transport — shared autos, e-rickshaws, minibuses |
| Profile | YAML config fully parameterising a run (`default` = TfL, `india`) |
