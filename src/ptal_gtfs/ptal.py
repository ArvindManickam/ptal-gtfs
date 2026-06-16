"""PTAL core computation: walk time, waiting time, EDF, Accessibility Index and bands.

Implements the TfL PTAL formulas (methodology §1.4-1.7) end to end. Given per-grid-point
walk distances to stops (the access table from ``ptal_gtfs.network.nearest_stops``) and the
per-(route, direction, stop) peak frequencies (from GTFS loading), it produces the
Accessibility Index and PTAL band for every grid point.

Formulas (all times in minutes; methodology §1):

    WT  = walk_distance / walk_speed                (§1.2; TfL walk_speed = 80 m/min)
    SWT = 0.5 x (60 / f) = 30 / f                    (§1.4; f = vehicles/hour in the peak)
    AWT = SWT + K                                    (§1.4; K = per-mode reliability factor)
    TAT = WT + AWT                                   (§1.5)
    EDF = 30 / TAT                                   (§1.5)
    AI_mode  = EDF_max + 0.5 x Sum(EDF_others)       (§1.6; per mode, after de-duplicating
                                                      each route to its best SAP, §1.3)
    AI_total = Sum(AI_mode)                          (§1.6)
    band     = AI_total mapped onto the PTAL table   (§1.7)

Parameters the methodology leaves to the config profile (walk speed, reliability factors,
band edges) are module constants here, overridable per call, until the profile system
(Phase 3) owns them. The default values reproduce the TfL method.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Profile, load_profile

# --------------------------------------------------------------------------------------
# TfL parameters (methodology §1). Defaults only; a config profile will own these later.
# --------------------------------------------------------------------------------------

DEFAULT_WALK_SPEED_M_PER_MIN = 80.0

DEFAULT_RELIABILITY_MIN: dict[str, float] = {
    "bus": 2.0,  # TfL bus
    "metro": 0.75,  # TfL Underground
    "rail": 0.75,  # TfL rail
    "tram": 0.75,  # TfL tram
}

# PTAL band table (§1.7): upper-inclusive AI edges and their labels. AI == 0 -> band "0".
TFL_BAND_EDGES: list[float] = [0.0, 2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 40.0, np.inf]
TFL_BAND_LABELS: list[str] = ["1a", "1b", "2", "3", "4", "5", "6a", "6b"]


# --------------------------------------------------------------------------------------
# Per-quantity formulas (vectorised: accept scalars or array-likes)
# --------------------------------------------------------------------------------------


def walk_time(distance_m, walk_speed_m_per_min: float = DEFAULT_WALK_SPEED_M_PER_MIN):
    """Walk time in minutes from a network distance in metres (methodology §1.2)."""
    return np.asarray(distance_m, dtype=float) / walk_speed_m_per_min


def scheduled_waiting_time(frequency_vph):
    """SWT in minutes: half the headway, ``0.5 x 60 / f = 30 / f`` (methodology §1.4)."""
    f = np.asarray(frequency_vph, dtype=float)
    return np.divide(30.0, f, out=np.full_like(f, np.inf), where=f > 0)


def average_waiting_time(swt, reliability_min):
    """AWT in minutes: SWT plus the reliability factor K (methodology §1.4)."""
    return np.asarray(swt, dtype=float) + np.asarray(reliability_min, dtype=float)


def equivalent_doorstep_frequency(total_access_time):
    """EDF: ``30 / TAT`` (methodology §1.5). TAT <= 0 yields 0."""
    tat = np.asarray(total_access_time, dtype=float)
    return np.divide(30.0, tat, out=np.zeros_like(tat), where=tat > 0)


def accessibility_index(edfs) -> float:
    """AI for one mode at one point: ``EDF_max + 0.5 x Sum(EDF_others)`` (methodology §1.6).

    Equivalent to ``0.5 x (EDF_max + Sum(EDF))``; the most attractive service counts in
    full and the rest at half weight.
    """
    edfs = np.asarray(edfs, dtype=float)
    if edfs.size == 0:
        return 0.0
    return float(edfs.max() + 0.5 * (edfs.sum() - edfs.max()))


def ptal_band(
    ai,
    band_edges: Sequence[float] = TFL_BAND_EDGES,
    band_labels: Sequence[str] = TFL_BAND_LABELS,
) -> np.ndarray:
    """Map Accessibility Index values to PTAL bands (methodology §1.7). AI <= 0 -> ``"0"``."""
    ai = np.asarray(ai, dtype=float)
    band = pd.cut(ai, bins=band_edges, labels=band_labels, right=True).astype("object")
    band = pd.Series(band)
    band[ai <= 0] = "0"
    return band.to_numpy()


# --------------------------------------------------------------------------------------
# End-to-end PTAL for a grid
# --------------------------------------------------------------------------------------


def _resolve_profile(profile: Profile | str | Path | None) -> Profile:
    """Normalise ``profile`` (object, shipped name, path, or ``None``) to a :class:`Profile`."""
    if profile is None:
        return load_profile("default")
    if isinstance(profile, Profile):
        return profile
    return load_profile(profile)


def compute_ptal(
    access: pd.DataFrame,
    frequencies: pd.DataFrame,
    *,
    profile: Profile | str | Path | None = None,
    route_keys: Sequence[str] = ("route_id", "direction_id"),
    all_poi_ids: Sequence | None = None,
) -> pd.DataFrame:
    """Accessibility Index and PTAL band for every grid point (methodology §1.3-1.7).

    Parameters
    ----------
    access:
        Walk distances per (grid point, stop): columns ``poi_id``, ``stop_id``, ``walk_m``
        (e.g. from :func:`ptal_gtfs.network.nearest_stops`). Per-mode access thresholds are
        assumed already applied upstream, so a stop appears only where its mode is reachable.
    frequencies:
        Peak per-(route, direction, stop) frequencies: columns ``stop_id``, ``mode``,
        ``frequency_vph`` plus the ``route_keys`` (e.g. ``GtfsData.frequencies``).
    profile:
        A :class:`~ptal_gtfs.config.Profile`, a shipped profile name (``"default"``,
        ``"india"``) or a path to a profile YAML. ``None`` uses the TfL ``default``. The
        profile supplies the walk speed, the reliability model (``static`` or
        ``deviation``) and the band table.
    route_keys:
        Columns identifying a "route" for de-duplication (§1.3); default route + direction.
    all_poi_ids:
        Optional full list of grid ``poi_id``s. Points with no reachable service are then
        included with AI 0 and band ``"0"``.

    Returns
    -------
    pandas.DataFrame
        One row per grid point: ``poi_id``, an ``ai_<mode>`` column per mode, ``ai`` (the
        total Accessibility Index) and ``ptal_band``.
    """
    profile = _resolve_profile(profile)
    reliability = profile.reliability
    route_keys = list(route_keys)

    df = access[["poi_id", "stop_id", "walk_m"]].merge(
        frequencies[["stop_id", "mode", "frequency_vph", *route_keys]],
        on="stop_id",
        how="inner",
    )

    missing = set(df["mode"].dropna().unique()) - set(reliability.by_mode)
    if missing:
        raise ValueError(
            f"profile '{profile.name}' has no reliability factor for mode(s): {sorted(missing)}"
        )

    # Reliability term K (minutes): a fixed per-mode value (static, TfL), or the scheduled
    # headway scaled by a per-mode deviation factor (deviation, India; methodology §3.3).
    factor = df["mode"].map(reliability.by_mode)

    if reliability.kind == "deviation":
        k = (60.0 / df["frequency_vph"]) * factor
    else:
        k = factor

    wt = walk_time(df["walk_m"], profile.walk_speed_m_per_min)
    swt = scheduled_waiting_time(df["frequency_vph"])
    df["edf"] = equivalent_doorstep_frequency(wt + average_waiting_time(swt, k))

    # §1.3 — keep each route's best SAP (largest EDF) per grid point and mode.
    best = df.groupby(["poi_id", "mode", *route_keys], as_index=False)["edf"].max()

    # §1.6 — AI per mode = EDF_max + 0.5 x sum(others) = 0.5 x (EDF_max + sum(EDF)).
    per_mode = best.groupby(["poi_id", "mode"])["edf"].agg(edf_max="max", edf_sum="sum")
    per_mode["ai_mode"] = 0.5 * (per_mode["edf_max"] + per_mode["edf_sum"])

    ai_total = per_mode.groupby("poi_id")["ai_mode"].sum().rename("ai")
    ai_wide = per_mode["ai_mode"].unstack("mode").add_prefix("ai_")

    result = ai_wide.join(ai_total)
    if all_poi_ids is not None:
        result = result.reindex(pd.Index(all_poi_ids, name="poi_id"))

    mode_cols = sorted(c for c in result.columns if c.startswith("ai_"))
    result[mode_cols] = result[mode_cols].fillna(0.0)
    result["ai"] = result["ai"].fillna(0.0)
    result["ptal_band"] = ptal_band(result["ai"], profile.bands.edges, profile.bands.labels)

    return result.reset_index()[["poi_id", *mode_cols, "ai", "ptal_band"]]
