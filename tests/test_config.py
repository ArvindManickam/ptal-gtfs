"""Tests for configuration profiles (``ptal_gtfs.config``)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ptal_gtfs.config import Profile, load_profile


def test_default_profile_is_tfl_static():
    p = load_profile("default")
    assert p.name == "default"
    assert p.walk_speed_m_per_min == 80.0
    assert p.reliability.kind == "static"
    assert p.reliability.by_mode["bus"] == 2.0
    assert p.reliability.by_mode["metro"] == 0.75


def test_india_profile_is_deviation():
    p = load_profile("india")
    assert p.reliability.kind == "deviation"
    assert p.reliability.by_mode["bus"] == 0.2
    assert p.reliability.by_mode["metro"] == 0.05


def test_band_edges_parse_infinity():
    p = load_profile("default")
    assert p.bands.edges[-1] == float("inf")
    assert len(p.bands.edges) == len(p.bands.labels) + 1


def test_invalid_reliability_kind_rejected():
    with pytest.raises(ValidationError):
        Profile.model_validate(
            {
                "name": "bad",
                "reliability": {"kind": "wobbly", "by_mode": {"bus": 1.0}},
                "bands": {"edges": [0.0, 1.0], "labels": ["1a"]},
            }
        )


def test_mismatched_band_lengths_rejected():
    with pytest.raises(ValidationError):
        Profile.model_validate(
            {
                "name": "bad",
                "reliability": {"kind": "static", "by_mode": {"bus": 1.0}},
                "bands": {"edges": [0.0, 1.0, 2.0], "labels": ["1a"]},  # needs 2 labels
            }
        )


def test_load_profile_from_path(tmp_path):
    yaml_text = (
        "name: custom\n"
        "walk_speed_m_per_min: 72.0\n"
        "reliability: {kind: static, by_mode: {bus: 1.5}}\n"
        "bands: {edges: [0.0, 5.0, .inf], labels: ['1a', '1b']}\n"
    )
    path = tmp_path / "custom.yaml"
    path.write_text(yaml_text)
    p = load_profile(path)
    assert p.name == "custom"
    assert p.walk_speed_m_per_min == 72.0


def test_unknown_profile_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("does-not-exist")
