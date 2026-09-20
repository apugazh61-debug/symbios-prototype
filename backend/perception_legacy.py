"""
perception.py
--------------
Turns a single webcam frame into pose landmarks, then turns a short
history of landmarks into a fatigue score (0-100).

Heuristic used for the hackathon MVP (not a clinical measure):
  - Movement speed: fast, frequent motion -> alert. A drop in speed
    over time -> possible fatigue.
  - Posture droop: shoulders sagging closer to the hips -> slouching,
    a common fatigue signal.
  - Micro-pauses: long stretches with near-zero movement between
    active work periods -> possible fatigue / micro-sleep.

These three signals are combined into one 0-100 score. Tune the
weights in FATIGUE_WEIGHTS once you see it running on real footage.
"""

import time
from collections import deque

import cv2
import mediapipe as mp
import numpy as np

mp_pose = mp.solutions.pose

# Landmarks we care about (MediaPipe Pose indices)
L_SHOULDER, R_SHOULDER = 11, 12
L_HIP, R_HIP = 23, 24
L_WRIST, R_WRIST = 15, 16

FATIGUE_WEIGHTS = {
    "speed_drop": 0.4,
    "posture_droop": 0.35,
    "micro_pause": 0.25,
}

HISTORY_SECONDS = 15  # rolling window used to judge "drop" in activity


class WorkerTracker:
    """
    Keeps a short rolling history of landmarks for ONE worker so we can
    detect trends (speed dropping, posture drooping) instead of judging
    a single frame in isolation.

    For the hackathon MVP we track a single worker (single webcam).
    To track multiple workers, keep one WorkerTracker per worker_id.
    """

    def __init__(self):
        self.pose = mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.history = deque()  # list of (timestamp, landmarks, wrist_speed)
        self.last_landmarks = None
        self.last_timestamp = None

    def _extract_landmarks(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self.pose.process(rgb)
        if not result.pose_landmarks:
            return None
        h, w, _ = frame_bgr.shape
        pts = {}
        for idx in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_WRIST, R_WRIST):
            lm = result.pose_landmarks.landmark[idx]
            pts[idx] = np.array([lm.x * w, lm.y * h])
        return pts

    def _wrist_speed(self, pts, dt):
        if self.last_landmarks is None or dt <= 0:
            return 0.0
        lw_speed = np.linalg.norm(pts[L_WRIST] - self.last_landmarks[L_WRIST]) / dt
        rw_speed = np.linalg.norm(pts[R_WRIST] - self.last_landmarks[R_WRIST]) / dt
        return float((lw_speed + rw_speed) / 2)

    def _posture_droop(self, pts):
        """Vertical shoulder-to-hip distance. Smaller -> more slouched."""
        shoulder_y = (pts[L_SHOULDER][1] + pts[R_SHOULDER][1]) / 2
        hip_y = (pts[L_HIP][1] + pts[R_HIP][1]) / 2
        return float(hip_y - shoulder_y)  # pixels; larger = more upright

    def _center(self, pts):
        """Rough body-center point, used for safety-zone checks."""
        return (pts[L_HIP] + pts[R_HIP]) / 2

    def process_frame(self, frame_bgr):
        """
        Call this once per incoming frame.
        Returns dict with fatigue_score, center point, and raw signals,
        or None if no person was detected in the frame.
        """
        now = time.time()
        pts = self._extract_landmarks(frame_bgr)
        if pts is None:
            return None

        dt = (now - self.last_timestamp) if self.last_timestamp else 0
        speed = self._wrist_speed(pts, dt)
        droop = self._posture_droop(pts)
        center = self._center(pts)

        self.history.append((now, speed, droop))
        while self.history and now - self.history[0][0] > HISTORY_SECONDS:
            self.history.popleft()

        fatigue_score = self._compute_fatigue_score()

        self.last_landmarks = pts
        self.last_timestamp = now

        return {
            "fatigue_score": fatigue_score,
            "center_norm": (
                float(center[0] / frame_bgr.shape[1]),
                float(center[1] / frame_bgr.shape[0]),
            ),
            "wrist_speed": speed,
            "posture_droop": droop,
        }

    def _compute_fatigue_score(self):
        if len(self.history) < 2:
            return 0.0

        speeds = [s for _, s, _ in self.history]
        droops = [d for _, _, d in self.history]

        # 1) Speed drop: compare first half vs second half of window
        mid = len(speeds) // 2
        early_avg = np.mean(speeds[:mid]) if mid > 0 else speeds[0]
        late_avg = np.mean(speeds[mid:])
        speed_drop = max(0.0, early_avg - late_avg) / (early_avg + 1e-6)
        speed_drop_score = min(1.0, speed_drop) * 100

        # 2) Posture droop: how much shoulder-hip distance shrank vs window max
        max_droop = max(droops)
        current_droop = droops[-1]
        droop_ratio = 1 - (current_droop / (max_droop + 1e-6))
        posture_score = min(1.0, max(0.0, droop_ratio)) * 100

        # 3) Micro-pauses: fraction of samples with near-zero speed
        still_frames = sum(1 for s in speeds if s < 5.0)
        pause_score = (still_frames / len(speeds)) * 100

        score = (
            FATIGUE_WEIGHTS["speed_drop"] * speed_drop_score
            + FATIGUE_WEIGHTS["posture_droop"] * posture_score
            + FATIGUE_WEIGHTS["micro_pause"] * pause_score
        )
        return round(min(100.0, score), 1)
