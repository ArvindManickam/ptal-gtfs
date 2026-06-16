"""Profile schema (pydantic) and loader.

A profile fully parameterises a PTAL run: the peak window, grid spacing, per-mode walk
access thresholds, walk speed, the reliability model, and the band table. Only the *inputs*
(feed paths, service date, boundary, output) stay outside the profile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

_PROFILE_DIR = Path(__file__).parent / "profiles"


class PeakWindow(BaseModel):
    """AM peak window the frequency is measured in (methodology §1.4)."""

    start: str = "08:15"
    end: str = "09:15"


class Grid(BaseModel):
    """POI grid settings (methodology §1.1)."""

    spacing_m: float = 100.0


class Reliability(BaseModel):
    """How the reliability term K (added to SWT to give AWT) is derived per mode.

    - ``static``: ``AWT = SWT + K`` with a fixed per-mode K in minutes (the TfL method).
    - ``deviation``: ``AWT = SWT + headway × factor`` with a per-mode deviation factor, so
      reliability worsens with headway (the India adaptation; methodology §3.3, D3).
    """

    kind: Literal["static", "deviation"]
    by_mode: dict[str, float]


class Bands(BaseModel):
    """PTAL band table: ``len(edges) == len(labels) + 1`` (methodology §1.7)."""

    edges: list[float]
    labels: list[str]

    @model_validator(mode="after")
    def _check_lengths(self) -> Bands:
        if len(self.edges) != len(self.labels) + 1:
            raise ValueError("bands.edges must have exactly one more entry than bands.labels")
        return self


class Profile(BaseModel):
    """A complete, validated configuration profile for a PTAL run."""

    name: str
    walk_speed_m_per_min: float = 80.0
    peak_window: PeakWindow = Field(default_factory=PeakWindow)
    grid: Grid = Field(default_factory=Grid)
    # Per-mode maximum network walk distance to a stop (the SAP access threshold), metres.
    access_m: dict[str, float] = Field(default_factory=dict)
    reliability: Reliability
    bands: Bands


def load_profile(source: str | Path) -> Profile:
    """Load a profile by shipped name (``"default"``, ``"india"``) or from a YAML path."""
    shipped = _PROFILE_DIR / f"{source}.yaml"
    if isinstance(source, str) and not Path(source).suffix and shipped.exists():
        path = shipped
    else:
        path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {source!r}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Profile.model_validate(data)
