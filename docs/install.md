# Installation

!!! warning "Not yet published"
    `ptal-gtfs` is pre-alpha and is not on PyPI yet. The command below will work
    once the first release is published.

## From PyPI (once released)

```bash
pip install ptal-gtfs
```

Requires **Python 3.10 or newer**.

## From source (development)

Clone the repository and install it in editable mode with the development extras:

```bash
git clone https://github.com/CHANGEME/ptal-gtfs.git
cd ptal-gtfs
pip install -e ".[dev]"
```

This pulls in the runtime dependencies (pandas, numpy, geopandas, shapely, pyproj,
osmnx, pandana, partridge, pydantic, pyyaml, folium) plus the test and lint tools.

## Building these docs locally

The documentation uses [MkDocs](https://www.mkdocs.org/) with the Material theme.
To preview it while you edit:

```bash
pip install -r docs/requirements.txt
mkdocs serve
```

Then open <http://127.0.0.1:8000>. The site rebuilds on save.
