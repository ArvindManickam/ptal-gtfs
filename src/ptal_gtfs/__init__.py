"""ptal-gtfs: Public Transport Accessibility Levels for Indian cities.

Library-first package computing PTAL from GTFS + OpenStreetMap, faithful to the
Transport for London method by default and parameterised for Indian conditions.

See ``docs/methodology.md`` for the definitive description of what is computed.
"""

from __future__ import annotations

from .config import Profile, load_profile
from .io.gtfs import (
    Feed,
    FeedIssue,
    FeedProfile,
    FeedReport,
    FeedSource,
    GtfsData,
    GtfsValidationError,
    check_feed,
    inspect,
    load_feed,
    load_feeds,
    profile_feed,
    profile_feeds,
)
from .ptal import compute_ptal

__version__ = "0.0.1"

__all__ = [
    "Feed",
    "FeedIssue",
    "FeedProfile",
    "FeedReport",
    "FeedSource",
    "GtfsData",
    "GtfsValidationError",
    "PTALAnalysis",
    "PTALResult",
    "Profile",
    "check_feed",
    "compute_ptal",
    "inspect",
    "load_feed",
    "load_feeds",
    "load_profile",
    "profile_feed",
    "profile_feeds",
    "__version__",
]


def __getattr__(name: str):
    # Expose the top-level workflow lazily so `import ptal_gtfs` does not pull the heavy
    # geo stack (osmnx/pandana) unless PTALAnalysis/PTALResult are actually used.
    if name in ("PTALAnalysis", "PTALResult"):
        from . import analysis

        globals()["PTALAnalysis"] = analysis.PTALAnalysis
        globals()["PTALResult"] = analysis.PTALResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
