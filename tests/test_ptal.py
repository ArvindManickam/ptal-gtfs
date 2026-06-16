"""Tests for the PTAL core (``ptal_gtfs.ptal``)."""

from __future__ import annotations

import numpy as np
import pytest

from ptal_gtfs.ptal import (
    accessibility_index,
    average_waiting_time,
    compute_ptal,
    equivalent_doorstep_frequency,
    ptal_band,
    scheduled_waiting_time,
    walk_time,
)


def _access(walk_m=80.0):
    import pandas as pd

    return pd.DataFrame({"poi_id": [0], "stop_id": ["S"], "walk_m": [walk_m]})


def _freqs(frequency_vph=6.0, mode="bus"):
    import pandas as pd

    return pd.DataFrame(
        {
            "stop_id": ["S"],
            "route_id": ["R"],
            "direction_id": [0],
            "mode": [mode],
            "frequency_vph": [frequency_vph],
        }
    )


# --- per-quantity formulas ---------------------------------------------------------


def test_formula_chain_values():
    assert float(walk_time(80.0)) == 1.0  # 80 m / 80 m/min
    assert float(scheduled_waiting_time(6.0)) == 5.0  # 30 / 6
    assert float(average_waiting_time(5.0, 2.0)) == 7.0  # SWT + K
    assert float(equivalent_doorstep_frequency(8.0)) == 3.75  # 30 / 8


def test_accessibility_index_weights_best_full_others_half():
    # best EDF full weight, the rest half: 4 + 0.5*(2 + 1) = 5.5
    assert accessibility_index([4.0, 2.0, 1.0]) == 5.5
    assert accessibility_index([3.0]) == 3.0  # single service -> its own EDF
    assert accessibility_index([]) == 0.0


def test_ptal_band_edges():
    bands = ptal_band([0.0, 0.01, 2.5, 2.51, 40.0, 41.0])
    assert list(bands) == ["0", "1a", "1a", "1b", "6a", "6b"]


# --- end-to-end, profile-driven ----------------------------------------------------


def test_default_static_profile_hand_calc():
    # WT 1, SWT 5, K(bus)=2 -> AWT 7, TAT 8, EDF 3.75, AI 3.75 -> band 1b
    res = compute_ptal(_access(), _freqs(frequency_vph=6.0))
    row = res.iloc[0]
    assert row["ai"] == pytest.approx(3.75)
    assert row["ptal_band"] == "1b"


def test_deviation_profile_differs_from_static():
    # f=3: SWT 10, headway 20. static K=2 -> EDF 30/13; deviation 0.2 -> K=4 -> EDF 2.0
    access, freqs = _access(), _freqs(frequency_vph=3.0)
    static = compute_ptal(access, freqs, profile="default").iloc[0]["ai"]
    deviation = compute_ptal(access, freqs, profile="india").iloc[0]["ai"]
    assert static == pytest.approx(30.0 / 13.0)
    assert deviation == pytest.approx(2.0)
    assert deviation < static  # India penalises the long headway more


def test_unknown_mode_without_factor_raises():
    with pytest.raises(ValueError, match="reliability factor"):
        compute_ptal(_access(), _freqs(mode="ferry"), profile="india")


def test_all_poi_ids_fills_unreachable_points_with_zero():
    # poi 1 has no access rows -> AI 0, band "0"
    res = compute_ptal(_access(), _freqs(), all_poi_ids=[0, 1])
    by_poi = dict(zip(res["poi_id"], res["ptal_band"], strict=True))
    assert by_poi[1] == "0"
    assert dict(zip(res["poi_id"], res["ai"], strict=True))[1] == 0.0


def test_modes_sum_at_full_weight():
    import pandas as pd

    # A bus and a metro route at the same stop: AI_total = AI_bus + AI_metro.
    access = pd.DataFrame({"poi_id": [0], "stop_id": ["S"], "walk_m": [80.0]})
    freqs = pd.DataFrame(
        {
            "stop_id": ["S", "S"],
            "route_id": ["B", "M"],
            "direction_id": [0, 0],
            "mode": ["bus", "metro"],
            "frequency_vph": [6.0, 12.0],
        }
    )
    res = compute_ptal(access, freqs)
    row = res.iloc[0]
    assert row["ai"] == pytest.approx(row["ai_bus"] + row["ai_metro"])
    assert np.isclose(row["ai_bus"], 3.75)  # same bus calc as before
