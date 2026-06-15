"""ptal-gtfs: Public Transport Accessibility Levels for Indian cities.

Library-first package computing PTAL from GTFS + OpenStreetMap, faithful to the
Transport for London method by default and parameterised for Indian conditions.

See ``docs/methodology.md`` for the definitive description of what is computed.
"""

from __future__ import annotations

from .io.gtfs import (
    Feed,
    FeedIssue,
    FeedReport,
    FeedSource,
    GtfsData,
    GtfsValidationError,
    check_feed,
    inspect,
    load_feed,
    load_feeds,
)

__version__ = "0.0.1"

__all__ = [
    "Feed",
    "FeedIssue",
    "FeedReport",
    "FeedSource",
    "GtfsData",
    "GtfsValidationError",
    "check_feed",
    "inspect",
    "load_feed",
    "load_feeds",
    "__version__",
]
