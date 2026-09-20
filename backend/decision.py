"""
decision.py
-----------
The orchestration "brain". For the hackathon MVP this is a clear,
explainable RULE-BASED engine — fast to build, easy to demo, and easy
to justify to judges ("here is exactly why it decided this").

The function signature is intentionally state-in / decision-out, so
you can swap `decide()` for a trained Stable-Baselines3 policy later
(reinforcement learning core, mentioned on the architecture slide)
without touching any other part of the system.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

FATIGUE_HIGH = 70
FATIGUE_MEDIUM = 40


@dataclass
class Decision:
    action: str          # "REASSIGN_TO_COBOT" | "MONITOR" | "ZONE_ALERT" | "NORMAL"
    severity: str         # "critical" | "warning" | "info"
    reason_tags: list      # machine-readable reasons, used by the copilot for explanations
    fatigue_score: float
    in_zone: bool
    zone_label: str | None


def decide(fatigue_score: float, zone) -> Decision:
    """
    zone: a Zone object (see zones.py) if the worker is inside one, else None.
    """
    in_zone = zone is not None
    zone_label = zone.label if zone else None

    if fatigue_score >= FATIGUE_HIGH and in_zone:
        return Decision(
            action="REASSIGN_TO_COBOT",
            severity="critical",
            reason_tags=["high_fatigue", "in_danger_zone"],
            fatigue_score=fatigue_score,
            in_zone=in_zone,
            zone_label=zone_label,
        )

    if fatigue_score >= FATIGUE_HIGH:
        return Decision(
            action="MONITOR",
            severity="warning",
            reason_tags=["high_fatigue"],
            fatigue_score=fatigue_score,
            in_zone=in_zone,
            zone_label=zone_label,
        )

    if in_zone:
        return Decision(
            action="ZONE_ALERT",
            severity="warning",
            reason_tags=["in_danger_zone"],
            fatigue_score=fatigue_score,
            in_zone=in_zone,
            zone_label=zone_label,
        )

    if fatigue_score >= FATIGUE_MEDIUM:
        return Decision(
            action="MONITOR",
            severity="info",
            reason_tags=["moderate_fatigue"],
            fatigue_score=fatigue_score,
            in_zone=in_zone,
            zone_label=zone_label,
        )

    return Decision(
        action="NORMAL",
        severity="info",
        reason_tags=["nominal"],
        fatigue_score=fatigue_score,
        in_zone=in_zone,
        zone_label=zone_label,
    )


def decision_to_dict(decision: Decision) -> dict:
    return asdict(decision)
