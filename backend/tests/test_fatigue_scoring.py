"""
tests/test_fatigue_scoring.py
-----------------------------
Unit tests for posture slump, stillness accumulation, and feature vector extraction.
"""

import time
import pytest
from perception.fatigue_scorer import FatigueScorer


def test_slump_angle_calculation():
    scorer = FatigueScorer()
    # Perfectly vertical: shoulders and hips have same X coordinate
    shoulders = (0.5, 0.3)
    hips = (0.5, 0.6)
    angle = scorer.compute_slump_angle(shoulders, hips)
    assert round(angle, 1) == 0.0

    # Tilted trunk (shoulders shifted horizontally by 0.15 relative to vertical span 0.3)
    # atan2(0.15, 0.3) = ~26.56 degrees
    shoulders_tilted = (0.65, 0.3)
    angle_tilted = scorer.compute_slump_angle(shoulders_tilted, hips)
    assert round(angle_tilted, 1) == pytest.approx(26.6, abs=0.5)


def test_fatigue_score_progression():
    scorer = FatigueScorer(window_seconds=10.0)

    # Initial frame
    score_1, feat_1 = scorer.update(
        center_norm=(0.5, 0.5),
        shoulders_mid=(0.5, 0.3),
        hips_mid=(0.5, 0.6)
    )
    assert score_1 == 0.0
    assert feat_1.slump_angle_deg == 0.0

    # Simulate series of slouching, stationary updates over 2 seconds
    time.sleep(0.05)
    for _ in range(5):
        score, feat = scorer.update(
            center_norm=(0.5, 0.5),
            shoulders_mid=(0.68, 0.32),  # heavy slump
            hips_mid=(0.5, 0.6)
        )
    
    # Slump angle should be elevated
    assert feat.slump_angle_deg > 20.0
    # Score should climb
    assert score > 0.0
    # Feature vector schema has all expected keys
    f_dict = feat.to_dict()
    assert "slump_angle_deg" in f_dict
    assert "stillness_duration_s" in f_dict
    assert "velocity_drop_ratio" in f_dict
