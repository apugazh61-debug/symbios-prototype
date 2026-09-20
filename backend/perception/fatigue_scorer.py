"""
perception/fatigue_scorer.py
----------------------------
Configurable, multi-signal industrial fatigue evaluation engine.
Supports deterministic scoring (v1) and exports structured feature vectors for ML models (v2).
"""

import time
import math
from collections import deque
from typing import List, Dict, Any, Tuple, Optional
from core.config import settings


class FatigueFeatureVector:
    """Standardized schema for offline ML retraining."""
    def __init__(
        self,
        slump_angle_deg: float,
        velocity_drop_ratio: float,
        stillness_duration_s: float,
        motion_energy: float,
        head_pitch_deg: float,
        timestamp: float
    ):
        self.slump_angle_deg = slump_angle_deg
        self.velocity_drop_ratio = velocity_drop_ratio
        self.stillness_duration_s = stillness_duration_s
        self.motion_energy = motion_energy
        self.head_pitch_deg = head_pitch_deg
        self.timestamp = timestamp

    def to_dict(self) -> Dict[str, float]:
        return {
            "slump_angle_deg": round(self.slump_angle_deg, 2),
            "velocity_drop_ratio": round(self.velocity_drop_ratio, 3),
            "stillness_duration_s": round(self.stillness_duration_s, 2),
            "motion_energy": round(self.motion_energy, 4),
            "head_pitch_deg": round(self.head_pitch_deg, 2),
            "timestamp": self.timestamp
        }


class FatigueScorer:
    """
    Evaluates rolling skeletal kinematics to produce a calibrated [0, 100] fatigue index.
    
    Model Breakdown:
      1. Postural Sag: Deviation of shoulder-to-hip alignment from standard baseline.
      2. Kinetic Decay: Abrupt drops from active work to immobility (micro-sleep / exhaustion marker).
      3. Stillness Accumulation: Prolonged static pose duration.
    """
    def __init__(
        self,
        window_seconds: float = settings.FATIGUE_WINDOW_SECONDS,
        slump_threshold: float = settings.FATIGUE_SLUMP_THRESHOLD_DEGREES,
        stillness_threshold: float = settings.FATIGUE_STILLNESS_THRESHOLD_SECONDS
    ):
        self.window_seconds = window_seconds
        self.slump_threshold = slump_threshold
        self.stillness_threshold = stillness_threshold

        # Rolling history buffers: (timestamp, x, y, slump_angle, head_pitch)
        self.history: deque = deque()
        self.still_since: Optional[float] = None
        self.peak_speed: float = 0.0

    def compute_slump_angle(self, shoulders_mid: Tuple[float, float], hips_mid: Tuple[float, float]) -> float:
        """
        Computes trunk inclination angle relative to the vertical axis.
        0 degrees = perfectly upright posture; > 20 degrees = significant forward/lateral slump.
        """
        dx = shoulders_mid[0] - hips_mid[0]
        dy = hips_mid[1] - shoulders_mid[1]  # positive upwards in image coordinates
        if dy <= 0:
            return 45.0  # severely bent over
        angle_rad = math.atan2(abs(dx), dy)
        return math.degrees(angle_rad)

    def update(
        self,
        center_norm: Tuple[float, float],
        shoulders_mid: Tuple[float, float],
        hips_mid: Tuple[float, float],
        nose_pt: Optional[Tuple[float, float]] = None
    ) -> Tuple[float, FatigueFeatureVector]:
        """
        Ingests a frame observation and updates fatigue scoring state.
        Returns (fatigue_score, feature_vector).
        """
        now = time.time()
        slump_deg = self.compute_slump_angle(shoulders_mid, hips_mid)

        head_pitch_deg = 0.0
        if nose_pt:
            # Estimate head drop relative to shoulder line
            head_pitch_deg = max(0.0, (nose_pt[1] - shoulders_mid[1]) * 100.0)

        # Append to sliding history
        self.history.append((now, center_norm[0], center_norm[1], slump_deg, head_pitch_deg))

        # Evict data older than window
        while self.history and self.history[0][0] < (now - self.window_seconds):
            self.history.popleft()

        if len(self.history) < 3:
            feat = FatigueFeatureVector(slump_deg, 0.0, 0.0, 0.0, head_pitch_deg, now)
            return 0.0, feat

        # 1. Compute instantaneous speeds and motion energy across window
        speeds = []
        for i in range(1, len(self.history)):
            dt = self.history[i][0] - self.history[i - 1][0]
            if dt > 0:
                dist = math.hypot(
                    self.history[i][1] - self.history[i - 1][1],
                    self.history[i][2] - self.history[i - 1][2]
                )
                speeds.append(dist / dt)

        current_speed = speeds[-1] if speeds else 0.0
        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0
        self.peak_speed = max(self.peak_speed * 0.98, max(speeds) if speeds else 0.0)

        # 2. Track stillness duration
        is_still = current_speed < 0.03  # movement below jitter noise
        if is_still:
            if self.still_since is None:
                self.still_since = now
            still_duration = now - self.still_since
        else:
            self.still_since = None
            still_duration = 0.0

        # 3. Compute speed decay ratio
        if self.peak_speed > 0.08:
            velocity_drop = max(0.0, 1.0 - (current_speed / self.peak_speed))
        else:
            velocity_drop = 0.0

        # 4. Synthesize calibrated fatigue score [0.0, 100.0]
        # Component A: Slump posture weight (up to 40 pts)
        posture_score = min(40.0, max(0.0, (slump_deg - 5.0) / (self.slump_threshold * 1.5)) * 40.0)

        # Component B: Stillness accumulation weight (up to 35 pts)
        stillness_score = min(35.0, (still_duration / self.stillness_threshold) * 35.0)

        # Component C: Abrupt kinetic drop weight (up to 25 pts)
        kinetic_drop_score = velocity_drop * 25.0 if still_duration > 2.0 else 0.0

        raw_score = posture_score + stillness_score + kinetic_drop_score
        calibrated_score = round(min(100.0, max(0.0, raw_score)), 1)

        feature_vec = FatigueFeatureVector(
            slump_angle_deg=slump_deg,
            velocity_drop_ratio=velocity_drop,
            stillness_duration_s=still_duration,
            motion_energy=avg_speed,
            head_pitch_deg=head_pitch_deg,
            timestamp=now
        )

        return calibrated_score, feature_vec
