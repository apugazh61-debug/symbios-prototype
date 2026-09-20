"""
tests/test_cobot_state_machine.py
----------------------------------
Unit and integration tests for the cobot fleet real state machine.
Covers:
  - All valid transitions (IDLE->ASSIGNED->ACTIVE->RETURNING->IDLE)
  - Guard rejection of invalid transitions (raises InvalidTransition)
  - Supervisor reset override from any state
  - API endpoint integration: /reset, /activate, /complete, /dock
  - State persists to DB and scheduler stays in sync
"""

import pytest
from fastapi.testclient import TestClient
from main import app
from core.database import SessionLocal
from models.cobot import Cobot, CobotStatus
from engine.cobot_scheduler import CobotScheduler, CobotRecord, InvalidTransition
from core.security import get_bootstrap_supervisor_password


# ─────────────────────────────────────────────
# Unit tests — CobotScheduler state machine
# ─────────────────────────────────────────────

@pytest.fixture
def scheduler():
    """Fresh isolated scheduler for unit tests (empty fleet)."""
    s = CobotScheduler()
    # Replace default seeded fleet with only the test cobot
    s._fleet.clear()
    s.register_cobot("test-cobot-01", "TestBot-Unit")
    return s


def test_idle_to_assigned(scheduler):
    rec = scheduler.assign("test-cobot-01", "worker-1", "Assembly Zone", "Handle payload")
    assert rec.status == CobotStatus.ASSIGNED.value
    assert rec.active_task == "Handle payload"
    assert rec.worker_id == "worker-1"
    assert rec.zone_name == "Assembly Zone"
    assert rec.assigned_at is not None


def test_assigned_to_active(scheduler):
    scheduler.assign("test-cobot-01", "worker-1", "Assembly Zone", "Handle payload")
    rec = scheduler.activate("test-cobot-01")
    assert rec.status == CobotStatus.ACTIVE.value


def test_active_to_returning(scheduler):
    scheduler.assign("test-cobot-01", "worker-1", "Assembly Zone", "Handle payload")
    scheduler.activate("test-cobot-01")
    rec = scheduler.complete("test-cobot-01")
    assert rec.status == CobotStatus.RETURNING.value


def test_returning_to_idle(scheduler):
    scheduler.assign("test-cobot-01", "worker-1", "Assembly Zone", "Handle payload")
    scheduler.activate("test-cobot-01")
    scheduler.complete("test-cobot-01")
    rec = scheduler.dock_home("test-cobot-01")
    assert rec.status == CobotStatus.IDLE.value
    assert rec.active_task is None
    assert rec.worker_id is None
    assert rec.zone_name is None
    assert rec.assigned_at is None


def test_full_state_machine_cycle(scheduler):
    """Walk through the complete lifecycle and verify each phase."""
    rec = scheduler.get_status("test-cobot-01")
    assert rec.status == CobotStatus.IDLE.value

    scheduler.assign("test-cobot-01", "w-01", "Zone-A", "Pallet handover")
    assert scheduler.get_status("test-cobot-01").status == CobotStatus.ASSIGNED.value

    scheduler.activate("test-cobot-01")
    assert scheduler.get_status("test-cobot-01").status == CobotStatus.ACTIVE.value

    scheduler.complete("test-cobot-01")
    assert scheduler.get_status("test-cobot-01").status == CobotStatus.RETURNING.value

    scheduler.dock_home("test-cobot-01")
    assert scheduler.get_status("test-cobot-01").status == CobotStatus.IDLE.value


def test_invalid_transition_idle_to_active_rejected(scheduler):
    """Cannot skip ASSIGNED; IDLE→ACTIVE must be rejected."""
    with pytest.raises(InvalidTransition) as exc_info:
        scheduler.activate("test-cobot-01")
    assert "IDLE" in str(exc_info.value)
    assert "ACTIVE" in str(exc_info.value)


def test_invalid_transition_idle_to_returning_rejected(scheduler):
    """Cannot skip ASSIGNED and ACTIVE; IDLE→RETURNING must be rejected."""
    with pytest.raises(InvalidTransition):
        scheduler.complete("test-cobot-01")


def test_invalid_transition_assigned_to_idle_rejected(scheduler):
    """ASSIGNED→IDLE without reset_to_idle is not a valid arc."""
    scheduler.assign("test-cobot-01", "w-01", "Zone-A", "task")
    with pytest.raises(InvalidTransition):
        scheduler.dock_home("test-cobot-01")


def test_supervisor_reset_from_any_state(scheduler):
    """Supervisor reset override must work from any state including ACTIVE."""
    scheduler.assign("test-cobot-01", "w-01", "Zone-A", "task")
    scheduler.activate("test-cobot-01")
    assert scheduler.get_status("test-cobot-01").status == CobotStatus.ACTIVE.value

    rec = scheduler.reset_to_idle("test-cobot-01")
    assert rec.status == CobotStatus.IDLE.value
    assert rec.active_task is None


def test_fault_transition_from_idle(scheduler):
    rec = scheduler.fault("test-cobot-01", "Servo overcurrent")
    assert rec.status == CobotStatus.FAULT.value
    assert "overcurrent" in rec.active_task


def test_fault_reset_to_idle(scheduler):
    scheduler.fault("test-cobot-01", "Servo overcurrent")
    rec = scheduler.reset_to_idle("test-cobot-01")
    assert rec.status == CobotStatus.IDLE.value


def test_estop_from_active_state(scheduler):
    scheduler.assign("test-cobot-01", "w-01", "Zone-A", "task")
    scheduler.activate("test-cobot-01")
    rec = scheduler.estop("test-cobot-01")
    assert rec.status == CobotStatus.ESTOP.value


def test_get_context_returns_idle_cobot(scheduler):
    ctx = scheduler.get_context()
    assert ctx.available_cobot_id == "test-cobot-01"
    assert ctx.cobot_status == CobotStatus.IDLE.value


def test_get_context_none_when_all_busy(scheduler):
    scheduler.assign("test-cobot-01", "w-01", "Zone-A", "task")
    ctx = scheduler.get_context()
    assert ctx.available_cobot_id is None
    assert ctx.cobot_status == CobotStatus.ACTIVE.value


def test_trigger_reassignment_legacy_shim(scheduler):
    """Legacy shim (used by decisions.py) drives IDLE→ASSIGNED."""
    result = scheduler.trigger_reassignment("test-cobot-01", "w-01", "Collab Zone")
    assert result["success"] is True
    assert scheduler.get_status("test-cobot-01").status == CobotStatus.ASSIGNED.value


def test_trigger_reassignment_fails_when_not_idle(scheduler):
    """Legacy shim returns failure dict when cobot is not IDLE."""
    scheduler.assign("test-cobot-01", "w-01", "Zone-A", "existing task")
    result = scheduler.trigger_reassignment("test-cobot-01", "w-02", "Zone-B")
    assert result["success"] is False


# ─────────────────────────────────────────────
# Integration tests — API endpoints
# ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def get_supervisor_headers(client) -> dict:
    res = client.post(
        "/api/v1/auth/token",
        data={"username": "supervisor@symbios.ai", "password": get_bootstrap_supervisor_password()}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_list_cobots_returns_status(client):
    res = client.get("/api/v1/cobots")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    statuses = {c["status"] for c in data}
    valid = {s.value for s in CobotStatus}
    assert all(s in valid for s in statuses), f"Unknown status found: {statuses - valid}"


def test_cobot_status_endpoint(client):
    res = client.get("/api/v1/cobots/cobot-cell-01/status")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert "current_task_description" in data


def test_api_reset_requires_auth(client):
    res = client.post("/api/v1/cobots/cobot-cell-01/reset")
    assert res.status_code == 401


def test_api_activate_requires_auth(client):
    res = client.post("/api/v1/cobots/cobot-cell-01/activate")
    assert res.status_code == 401


def test_api_full_state_machine_cycle_via_endpoints(client):
    """
    Integration test: supervisor drives cobot through the full cycle via HTTP endpoints.
    Resets first to guarantee IDLE start, then:  IDLE → ASSIGNED (via decision trigger)
    → ACTIVE (/activate) → RETURNING (/complete) → IDLE (/dock).
    """
    headers = get_supervisor_headers(client)

    # 1. Ensure IDLE
    res = client.post("/api/v1/cobots/cobot-cell-01/reset", headers=headers)
    assert res.status_code == 200
    assert res.json()["new_status"] == CobotStatus.IDLE.value

    # 2. Manually drive IDLE→ASSIGNED by calling the scheduler directly
    from engine.cobot_scheduler import cobot_scheduler
    cobot_scheduler.assign("cobot-cell-01", "w-test", "Test Zone", "Integration task")

    # Also update DB manually to ASSIGNED for subsequent endpoint calls
    db = SessionLocal()
    try:
        cobot = db.query(Cobot).filter(Cobot.id == "cobot-cell-01").first()
        if cobot:
            cobot.status = CobotStatus.ASSIGNED.value
            cobot.current_task_description = "Integration task"
            db.commit()
    finally:
        db.close()

    # 3. ASSIGNED → ACTIVE
    res = client.post("/api/v1/cobots/cobot-cell-01/activate", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["new_status"] == CobotStatus.ACTIVE.value

    # 4. ACTIVE → RETURNING
    res = client.post("/api/v1/cobots/cobot-cell-01/complete", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["new_status"] == CobotStatus.RETURNING.value

    # 5. RETURNING → IDLE
    res = client.post("/api/v1/cobots/cobot-cell-01/dock", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["new_status"] == CobotStatus.IDLE.value

    # 6. Confirm DB status is IDLE
    db = SessionLocal()
    try:
        cobot = db.query(Cobot).filter(Cobot.id == "cobot-cell-01").first()
        assert cobot.status == CobotStatus.IDLE.value
        assert cobot.current_task_description is None
    finally:
        db.close()


def test_api_invalid_transition_returns_409(client):
    """Attempting ACTIVE→DOCK skipping RETURNING should return 409 Conflict."""
    headers = get_supervisor_headers(client)

    # Set cobot to ACTIVE in scheduler (reset first, then assign+activate)
    from engine.cobot_scheduler import cobot_scheduler
    cobot_scheduler.reset_to_idle("cobot-cell-01")
    cobot_scheduler.assign("cobot-cell-01", "w-t", "Zone-X", "test")
    cobot_scheduler.activate("cobot-cell-01")

    # Try to dock directly from ACTIVE — should be 409
    res = client.post("/api/v1/cobots/cobot-cell-01/dock", headers=headers)
    assert res.status_code == 409
    assert "ACTIVE" in res.json()["detail"]

    # Clean up
    cobot_scheduler.reset_to_idle("cobot-cell-01")
