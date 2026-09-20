"""
tests/test_decision_engine.py
-----------------------------
Unit tests for deterministic safety decision engine and cobot reallocation logic.
"""

import pytest
from models.decision import SafetyAction
from engine.rules_v1 import RuleBasedDecisionEngine
from engine.interface import WorkerStateContext, CobotContext


@pytest.fixture
def engine():
    return RuleBasedDecisionEngine(
        fatigue_monitor_threshold=35.0,
        fatigue_critical_threshold=65.0
    )


def test_normal_operation(engine):
    worker = WorkerStateContext(
        worker_id="Op-01",
        fatigue_score=10.0,
        slump_angle=5.0,
        stillness_seconds=0.5,
        in_zone=False
    )
    cobot = CobotContext(cobot_status="IDLE")

    res = engine.evaluate(worker, cobot)
    assert res.action == SafetyAction.NORMAL
    assert res.severity == "info"
    assert res.reassign_cobot is False


def test_moderate_fatigue_monitor(engine):
    worker = WorkerStateContext(
        worker_id="Op-01",
        fatigue_score=45.0,
        slump_angle=18.0,
        stillness_seconds=2.0,
        in_zone=False
    )
    cobot = CobotContext(cobot_status="IDLE")

    res = engine.evaluate(worker, cobot)
    assert res.action == SafetyAction.MONITOR
    assert res.severity == "warning"
    assert res.reassign_cobot is False


def test_zone_breach_without_fatigue(engine):
    worker = WorkerStateContext(
        worker_id="Op-01",
        fatigue_score=15.0,
        slump_angle=4.0,
        stillness_seconds=0.2,
        in_zone=True,
        zone_label="Robotic Envelope",
        zone_risk_tier="high",
        reassignment_eligible=True
    )
    cobot = CobotContext(cobot_status="IDLE")

    res = engine.evaluate(worker, cobot)
    assert res.action == SafetyAction.ZONE_ALERT
    assert res.severity == "critical"
    assert res.reassign_cobot is False


def test_fatigue_and_zone_cobot_reassignment(engine):
    worker = WorkerStateContext(
        worker_id="Op-01",
        fatigue_score=55.0,
        slump_angle=24.0,
        stillness_seconds=6.0,
        in_zone=True,
        zone_label="Collaborative Cell",
        zone_risk_tier="high",
        reassignment_eligible=True
    )
    cobot = CobotContext(
        available_cobot_id="cobot-01",
        cobot_name="UR10e-Arm",
        cobot_status="IDLE"
    )

    res = engine.evaluate(worker, cobot)
    assert res.action == SafetyAction.REASSIGN_TO_COBOT
    assert res.severity == "critical"
    assert res.reassign_cobot is True
    assert res.assigned_cobot_id == "cobot-01"


def test_critical_zone_emergency_stop(engine):
    worker = WorkerStateContext(
        worker_id="Op-01",
        fatigue_score=5.0,
        slump_angle=2.0,
        stillness_seconds=0.0,
        in_zone=True,
        zone_label="High Voltage Switchgear",
        zone_risk_tier="critical"
    )
    cobot = CobotContext(cobot_status="IDLE")

    res = engine.evaluate(worker, cobot)
    assert res.action == SafetyAction.ESTOP
    assert res.severity == "critical"
    assert "ESTOP" in res.rule_fired
