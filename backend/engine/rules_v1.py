"""
engine/rules_v1.py
------------------
Production-grade deterministic safety rules engine (v1).
Evaluates worker fatigue, polygon zone risk tier, and cobot resource queues.
"""

from core.config import settings
from models.decision import SafetyAction
from engine.interface import DecisionEngineBase, WorkerStateContext, CobotContext, SafetyDecisionResult


class RuleBasedDecisionEngine(DecisionEngineBase):
    """
    Deterministic Safety & Reallocation Rule Engine.
    Configurable via thresholds in core.config.Settings.
    """
    def __init__(
        self,
        fatigue_monitor_threshold: float = settings.FATIGUE_SCORE_MONITOR_THRESHOLD,
        fatigue_critical_threshold: float = settings.FATIGUE_SCORE_CRITICAL_THRESHOLD
    ):
        self.fatigue_monitor_threshold = fatigue_monitor_threshold
        self.fatigue_critical_threshold = fatigue_critical_threshold

    def evaluate(self, worker: WorkerStateContext, cobot: CobotContext) -> SafetyDecisionResult:
        # Rule 1: Extreme Hazard Zone Breach (Risk Tier = CRITICAL)
        if worker.in_zone and worker.zone_risk_tier == "critical":
            return SafetyDecisionResult(
                action=SafetyAction.ESTOP,
                severity="critical",
                rule_fired="RULE_CRITICAL_ZONE_BREACH_ESTOP",
                reason=f"Worker entered critical danger boundary '{worker.zone_label}'. Emergency robotic halt triggered.",
                engine_version="rule_v1",
                metadata={"zone": worker.zone_label, "risk": worker.zone_risk_tier}
            )

        # Rule 2: Fatigue + Collaborative Reassignment Zone
        # Worker is significantly fatigued AND in a zone eligible for cobot take-over
        if worker.fatigue_score >= self.fatigue_monitor_threshold and worker.in_zone and worker.reassignment_eligible:
            cobot_available = cobot.cobot_status == "IDLE"
            if cobot_available:
                return SafetyDecisionResult(
                    action=SafetyAction.REASSIGN_TO_COBOT,
                    severity="critical",
                    rule_fired="RULE_FATIGUE_ZONE_COBOT_REASSIGN",
                    reassign_cobot=True,
                    assigned_cobot_id=cobot.available_cobot_id,
                    assigned_cobot_name=cobot.cobot_name,
                    reason=f"Elevated fatigue ({worker.fatigue_score}) inside collaborative envelope '{worker.zone_label}'. Task dynamically reallocated to {cobot.cobot_name or 'Cobot'}.",
                    engine_version="rule_v1",
                    metadata={"fatigue": worker.fatigue_score, "zone": worker.zone_label, "cobot": cobot.cobot_name}
                )
            else:
                # Cobot is currently unavailable/busy -> Alert supervisor immediately
                return SafetyDecisionResult(
                    action=SafetyAction.ZONE_ALERT,
                    severity="critical",
                    rule_fired="RULE_FATIGUE_ZONE_COBOT_BUSY_ALERT",
                    reason=f"Elevated fatigue ({worker.fatigue_score}) inside '{worker.zone_label}', but cobots are currently busy. Immediate supervisor intervention required.",
                    engine_version="rule_v1",
                    metadata={"fatigue": worker.fatigue_score, "zone": worker.zone_label}
                )

        # Rule 3: Physical Zone Breach without significant fatigue
        if worker.in_zone:
            return SafetyDecisionResult(
                action=SafetyAction.ZONE_ALERT,
                severity="warning" if worker.zone_risk_tier != "high" else "critical",
                rule_fired="RULE_PROXIMITY_ZONE_BREACH",
                reason=f"Worker entered safety boundary '{worker.zone_label}'. Keep clear during active equipment cycles.",
                engine_version="rule_v1",
                metadata={"zone": worker.zone_label, "risk": worker.zone_risk_tier}
            )

        # Rule 4: High Fatigue Warning in Safe Zone
        if worker.fatigue_score >= self.fatigue_critical_threshold:
            return SafetyDecisionResult(
                action=SafetyAction.MONITOR,
                severity="warning",
                rule_fired="RULE_SEVERE_FATIGUE_MONITOR",
                reason=f"Severe worker exhaustion detected (score: {worker.fatigue_score}). Mandatory hydration/micro-break recommended.",
                engine_version="rule_v1",
                metadata={"fatigue": worker.fatigue_score, "slump": worker.slump_angle}
            )

        # Rule 5: Moderate Fatigue
        if worker.fatigue_score >= self.fatigue_monitor_threshold:
            return SafetyDecisionResult(
                action=SafetyAction.MONITOR,
                severity="warning",
                rule_fired="RULE_MODERATE_FATIGUE_MONITOR",
                reason=f"Worker displaying early fatigue signals (score: {worker.fatigue_score}). Keep monitoring posture.",
                engine_version="rule_v1",
                metadata={"fatigue": worker.fatigue_score}
            )

        # Rule 6: Normal Operation
        return SafetyDecisionResult(
            action=SafetyAction.NORMAL,
            severity="info",
            rule_fired="RULE_NORMAL_OPERATION",
            reason="Worker kinematics normal, clear of safety hazard zones.",
            engine_version="rule_v1",
            metadata={"fatigue": worker.fatigue_score}
        )
