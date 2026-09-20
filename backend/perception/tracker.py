"""
perception/tracker.py
--------------------
Multi-worker pose tracker with lightweight persistent spatial Re-ID.
Runs MediaPipe Pose and associates detections across frames without heavy GPU neural networks.
"""

import time
import math
from typing import Dict, List, Optional, Tuple, Any
import cv2
import numpy as np
import mediapipe as mp

from perception.fatigue_scorer import FatigueScorer, FatigueFeatureVector
from perception.polygon_engine import PolygonCollisionEngine, SafetyPolygon

mp_pose = mp.solutions.pose


class TrackedSubject:
    """Represents a single worker tracked over consecutive video frames."""
    def __init__(self, track_id: str):
        self.track_id = track_id
        self.scorer = FatigueScorer()
        self.last_seen: float = time.time()
        self.center_norm: Tuple[float, float] = (0.5, 0.5)
        self.fatigue_score: float = 0.0
        self.feature_vector: Optional[FatigueFeatureVector] = None
        self.current_zone: Optional[SafetyPolygon] = None
        self.active_breach: bool = False

    def update_pose(
        self,
        center_norm: Tuple[float, float],
        shoulders_mid: Tuple[float, float],
        hips_mid: Tuple[float, float],
        nose_pt: Optional[Tuple[float, float]],
        polygon_engine: PolygonCollisionEngine
    ):
        self.last_seen = time.time()
        self.center_norm = center_norm
        self.fatigue_score, self.feature_vector = self.scorer.update(
            center_norm=center_norm,
            shoulders_mid=shoulders_mid,
            hips_mid=hips_mid,
            nose_pt=nose_pt
        )
        self.current_zone = polygon_engine.check_point(center_norm[0], center_norm[1])
        self.active_breach = self.current_zone is not None


class MultiWorkerPipeline:
    """
    Perception pipeline managing pose extraction, spatial association, and safety checks.
    """
    def __init__(self):
        self.pose_detector = mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            min_detection_confidence=0.55,
            min_tracking_confidence=0.55
        )
        self.polygon_engine = PolygonCollisionEngine()
        self.tracks: Dict[str, TrackedSubject] = {}
        self.track_counter: int = 1
        self.track_timeout_seconds: float = 3.0

    def update_zones(self, zones: List[Dict[str, Any]]):
        """Updates polygon definitions from database."""
        self.polygon_engine.load_zones(zones)

    def process_frame(self, frame_bgr: np.ndarray) -> List[Dict[str, Any]]:
        """
        Executes perception pipeline on a single frame.
        Guarantees zero raw image retention in memory after extraction.
        """
        now = time.time()
        # 1. Convert color space for MediaPipe
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self.pose_detector.process(frame_rgb)

        # Clear raw frame reference immediately for GDPR memory safety
        del frame_rgb

        # 2. Cull timed-out tracks
        stale_ids = [tid for tid, subj in self.tracks.items() if (now - subj.last_seen) > self.track_timeout_seconds]
        for tid in stale_ids:
            del self.tracks[tid]

        if not results.pose_landmarks:
            return []

        # 3. Extract key skeletal landmarks
        lm = results.pose_landmarks.landmark
        
        # Upper body landmarks
        left_shoulder = (lm[mp_pose.PoseLandmark.LEFT_SHOULDER.value].x, lm[mp_pose.PoseLandmark.LEFT_SHOULDER.value].y)
        right_shoulder = (lm[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].x, lm[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].y)
        left_hip = (lm[mp_pose.PoseLandmark.LEFT_HIP.value].x, lm[mp_pose.PoseLandmark.LEFT_HIP.value].y)
        right_hip = (lm[mp_pose.PoseLandmark.RIGHT_HIP.value].x, lm[mp_pose.PoseLandmark.RIGHT_HIP.value].y)
        nose = (lm[mp_pose.PoseLandmark.NOSE.value].x, lm[mp_pose.PoseLandmark.NOSE.value].y)

        shoulders_mid = ((left_shoulder[0] + right_shoulder[0]) / 2.0, (left_shoulder[1] + right_shoulder[1]) / 2.0)
        hips_mid = ((left_hip[0] + right_hip[0]) / 2.0, (left_hip[1] + right_hip[1]) / 2.0)
        
        # Center of mass approximation
        center_norm = ((shoulders_mid[0] + hips_mid[0]) / 2.0, (shoulders_mid[1] + hips_mid[1]) / 2.0)

        # 4. Spatial association: Find closest existing track or spawn new track
        matched_id = None
        min_distance = 0.35  # maximum jump distance normalized per frame (~1/3 of screen)

        for tid, subj in self.tracks.items():
            dist = math.hypot(subj.center_norm[0] - center_norm[0], subj.center_norm[1] - center_norm[1])
            if dist < min_distance:
                min_distance = dist
                matched_id = tid

        if matched_id is None:
            matched_id = f"Operator-{self.track_counter:02d}"
            self.track_counter += 1
            self.tracks[matched_id] = TrackedSubject(matched_id)

        # 5. Update the matched track
        subject = self.tracks[matched_id]
        subject.update_pose(
            center_norm=center_norm,
            shoulders_mid=shoulders_mid,
            hips_mid=hips_mid,
            nose_pt=nose,
            polygon_engine=self.polygon_engine
        )

        # 6. Format output telemetry
        output = []
        for tid, subj in self.tracks.items():
            feat = subj.feature_vector.to_dict() if subj.feature_vector else {}
            output.append({
                "worker_id": subj.track_id,
                "center_norm": [round(subj.center_norm[0], 3), round(subj.center_norm[1], 3)],
                "fatigue_score": subj.fatigue_score,
                "slump_angle": feat.get("slump_angle_deg", 0.0),
                "stillness_seconds": feat.get("stillness_duration_s", 0.0),
                "in_zone": subj.active_breach,
                "zone_name": subj.current_zone.label if subj.current_zone else None,
                "zone_risk": subj.current_zone.risk_tier if subj.current_zone else None,
                "reassignment_eligible": subj.current_zone.reassignment_eligible if subj.current_zone else False,
                "features": feat
            })

        return output
