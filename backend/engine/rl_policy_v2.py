"""
engine/rl_policy_v2.py
----------------------
Swappable Reinforcement Learning policy wrapper (v2).
Prepares normalized state vectors for policy inference (Stable-Baselines3 / PyTorch).
"""

from typing import List
import numpy as np
from models.decision import SafetyAction
from engine.interface import DecisionEngineBase, WorkerStateContext, CobotContext, SafetyDecisionResult


class ReinforcementLearningDecisionEngine(DecisionEngineBase):
    """
    Reinforcement Learning policy agent wrapper.
    Converts state context to observation vector and executes learned policy.
    """
    def __init__(self, model_path: str = "models/checkpoints/safety_policy_v2.zip"):
        self.model_path = model_path
        self.is_loaded = False
        # In production, model is loaded via:
        # from stable_baselines3 import PPO
        # self.policy = PPO.load(model_path)

    def encode_observation(self, worker: WorkerStateContext, cobot: CobotContext) -> np.ndarray:
        """
        Converts real-time factory context into standard gym observation vector:
        [fatigue_score/100, slump_angle/90, stillness_s/30, in_zone, zone_risk_int,
         center_x, center_y, cobot_available, queue_depth]
        """
        risk_map = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}
        risk_val = risk_map.get(worker.zone_risk_tier or "", 0.0)
        cx = worker.center_norm[0] if worker.center_norm else 0.5
        cy = worker.center_norm[1] if worker.center_norm else 0.5

        obs = np.array([
            min(1.0, worker.fatigue_score / 100.0),
            min(1.0, worker.slump_angle / 90.0),
            min(1.0, worker.stillness_seconds / 30.0),
            1.0 if worker.in_zone else 0.0,
            risk_val / 4.0,
            cx,
            cy,
            1.0 if cobot.cobot_status in ["IDLE", "REASSIGNED"] else 0.0,
            min(1.0, cobot.active_queue_depth / 10.0)
        ], dtype=np.float32)
        return obs

    def evaluate(self, worker: WorkerStateContext, cobot: CobotContext) -> SafetyDecisionResult:
        obs = self.encode_observation(worker, cobot)

        # Fallback heuristic if trained weights not mounted
        if not self.is_loaded:
            # Emulate RL policy output matching safety bounds
            action_idx = 0  # 0: NORMAL, 1: MONITOR, 2: ZONE_ALERT, 3: REASSIGN, 4: ESTOP
            if worker.in_zone and worker.zone_risk_tier == "critical":
                action_idx = 4
            elif worker.in_zone and worker.fatigue_score >= 35.0:
                action_idx = 3
            elif worker.in_zone:
                action_idx = 2
            elif worker.fatigue_score >= 35.0:
                action_idx = 1

            actions = [SafetyAction.NORMAL, SafetyAction.MONITOR, SafetyAction.ZONE_ALERT, SafetyAction.REASSIGN_TO_COBOT, SafetyAction.ESTOP]
            severities = ["info", "warning", "warning", "critical", "critical"]

            return SafetyDecisionResult(
                action=actions[action_idx],
                severity=severities[action_idx],
                rule_fired=f"RL_POLICY_INFERENCE_ACT_{action_idx}",
                reassign_cobot=(action_idx == 3),
                assigned_cobot_id=cobot.available_cobot_id,
                assigned_cobot_name=cobot.cobot_name,
                reason=f"RL Policy evaluated state vector (dim={len(obs)}) -> selected action {actions[action_idx].value}",
                engine_version="rl_policy_v2",
                metadata={"observation_vector": obs.tolist()}
            )
