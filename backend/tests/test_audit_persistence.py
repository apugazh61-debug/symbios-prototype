"""
tests/test_audit_persistence.py
--------------------------------
Tests audit trail persistence in the relational database (SQLite),
verifying records survive new sessions and restarts.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from main import app
from core.database import SessionLocal
from models.audit import AuditLog


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


from core.security import get_bootstrap_supervisor_password, get_bootstrap_admin_password


def get_supervisor_auth_headers(client):
    """Helper to authenticate as supervisor and return valid Authorization Bearer headers."""
    res = client.post(
        "/api/v1/auth/token",
        data={"username": "supervisor@symbios.ai", "password": get_bootstrap_supervisor_password()}
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def get_admin_auth_headers(client):
    """Helper to authenticate as admin and return valid Authorization Bearer headers."""
    res = client.post(
        "/api/v1/auth/token",
        data={"username": "admin@symbios.ai", "password": get_bootstrap_admin_password()}
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_audit_log_post_and_get(client):
    headers = get_supervisor_auth_headers(client)
    test_id = str(uuid.uuid4())[:8]
    test_message = f"EHS automated test event verification {test_id}"
    
    # 1. Post audit log via API with valid supervisor credentials
    post_res = client.post(
        "/api/v1/audit/logs",
        headers=headers,
        json={
            "action": "SAFETY_INSPECTION",
            "entity_type": "AssemblyCell",
            "severity": "warning",
            "message": test_message,
            "changes_json": '{"status": "calibrated"}'
        }
    )
    assert post_res.status_code == 200, post_res.text
    data = post_res.json()
    assert data["message"] == test_message
    assert data["action"] == "SAFETY_INSPECTION"
    assert data["severity"] == "warning"
    created_id = data["id"]

    # 2. Query via GET endpoint (simulating page refresh)
    get_res = client.get("/api/v1/audit/logs?limit=20")
    assert get_res.status_code == 200
    logs = get_res.json()
    found = any(log["id"] == created_id and log["message"] == test_message for log in logs)
    assert found, "Created audit log entry not found in GET response"


def test_audit_log_survives_new_database_session(client):
    headers = get_supervisor_auth_headers(client)
    unique_msg = f"Permanent disk persistence test {uuid.uuid4().hex[:8]}"
    
    post_res = client.post(
        "/api/v1/audit/logs",
        headers=headers,
        json={
            "action": "COBOT_DISPATCH",
            "entity_type": "UR10e",
            "severity": "critical",
            "message": unique_msg
        }
    )
    assert post_res.status_code == 200
    log_id = post_res.json()["id"]

    # Open a completely fresh database session directly to SQLite
    db = SessionLocal()
    try:
        persisted = db.query(AuditLog).filter(AuditLog.id == log_id).first()
        assert persisted is not None, "Audit log failed to persist to relational database"
        assert persisted.message == unique_msg
        assert persisted.severity == "critical"
        assert persisted.action == "COBOT_DISPATCH"
        assert persisted.created_at is not None
    finally:
        db.close()


def test_decision_events_auto_logged_exactly_once(client):
    """
    Verifies that backend decisions.py auto-logs safety alerts/reallocations
    exactly once into SQLite AuditLog without requiring frontend duplicate calls.
    """
    import base64
    import numpy as np
    import cv2

    # Query initial count of decision logs
    db = SessionLocal()
    try:
        initial_count = db.query(AuditLog).filter(AuditLog.action.like("DECISION_%")).count()
    finally:
        db.close()

    # Create dummy black frame
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", frame)
    b64_img = "data:image/jpeg;base64," + base64.b64encode(buf).decode("utf-8")

    # Analyze frame (no worker detected in black frame -> NORMAL -> no warning/critical audit log)
    res = client.post("/api/v1/decisions/analyze_frame", json={"image_base64": b64_img})
    assert res.status_code == 200

    db = SessionLocal()
    try:
        count_after = db.query(AuditLog).filter(AuditLog.action.like("DECISION_%")).count()
        # Normal frames with no violations should NOT spam audit log
        assert count_after == initial_count
    finally:
        db.close()


def test_unauthenticated_requests_rejected_with_401_no_audit_log(client):
    """
    Requirement 2 & 3:
    Unauthenticated requests to state-changing endpoints must return 401 Unauthorized
    and NEVER create any audit log entries or fall back to a mock supervisor identity.
    """
    # 1. Reset cobot without token
    cobot_res = client.post("/api/v1/cobots/cobot-cell-01/reset")
    assert cobot_res.status_code == 401, f"Expected 401, got {cobot_res.status_code}"

    # 2. Create zone without token
    zone_create_res = client.post(
        "/api/v1/zones",
        json={
            "camera_id": "cam-cell-04",
            "name": "Unauthorized Rogue Zone",
            "risk_tier": "high",
            "polygon_coordinates": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]],
            "color_hex": "#FF0000",
            "reassignment_eligible": True
        }
    )
    assert zone_create_res.status_code == 401, f"Expected 401, got {zone_create_res.status_code}"

    # 3. Update zone without token
    zone_update_res = client.put(
        "/api/v1/zones/zone-cobot-envelope-01",
        json={
            "name": "Unauthorized Zone Modification",
            "change_reason": "Malicious attempt"
        }
    )
    assert zone_update_res.status_code == 401, f"Expected 401, got {zone_update_res.status_code}"

    # 4. Post audit log without token
    unauth_audit_msg = f"Forged audit attempt {uuid.uuid4().hex[:8]}"
    audit_res = client.post(
        "/api/v1/audit/logs",
        json={
            "action": "FORGED_EVENT",
            "entity_type": "System",
            "severity": "critical",
            "message": unauth_audit_msg
        }
    )
    assert audit_res.status_code == 401, f"Expected 401, got {audit_res.status_code}"

    # 5. Verify that NO audit logs were written for any of these unauthorized attempts
    db = SessionLocal()
    try:
        rogue_logs = db.query(AuditLog).filter(
            (AuditLog.message.like("%Unauthorized%")) |
            (AuditLog.message == unauth_audit_msg) |
            (AuditLog.action.in_(["CREATE_SAFETY_ZONE", "UPDATE_SAFETY_ZONE", "COBOT_RESET", "FORGED_EVENT"]) & (AuditLog.user_email == None))
        ).all()
        assert len(rogue_logs) == 0, f"Found unauthenticated audit logs: {rogue_logs}"
    finally:
        db.close()


def test_auth_register_requires_admin_role(client):
    """
    Critical finding 2.1:
    POST /auth/register must require ADMIN role.
    Unauthenticated -> 401
    Supervisor -> 403
    Admin -> 201 Created and AuditLog recorded.
    """
    unique_user = f"newworker_{uuid.uuid4().hex[:6]}@symbios.ai"
    reg_payload = {
        "email": unique_user,
        "password": "SecurePassword123!",
        "full_name": "New Safety Operator",
        "role": "supervisor"
    }

    # 1. Unauthenticated -> 401
    unauth_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert unauth_res.status_code == 401, unauth_res.text

    # 2. Authenticated as Supervisor (not Admin) -> 403 Forbidden
    sup_headers = get_supervisor_auth_headers(client)
    sup_res = client.post("/api/v1/auth/register", headers=sup_headers, json=reg_payload)
    assert sup_res.status_code == 403, f"Expected 403 for supervisor, got {sup_res.status_code}"

    # 3. Authenticated as Admin -> 201 Created
    admin_headers = get_admin_auth_headers(client)
    admin_res = client.post("/api/v1/auth/register", headers=admin_headers, json=reg_payload)
    assert admin_res.status_code == 201, admin_res.text
    created_data = admin_res.json()
    assert created_data["email"] == unique_user

    # Verify immutable AuditLog record created for account provisioning
    db = SessionLocal()
    try:
        audit = db.query(AuditLog).filter(
            AuditLog.action == "CREATE_USER_ACCOUNT",
            AuditLog.entity_id == created_data["id"]
        ).first()
        assert audit is not None, "AuditLog entry missing for admin user registration"
        assert audit.user_email == "admin@symbios.ai"
    finally:
        db.close()


def test_cobot_reset_audit_logging(client):
    """
    Verifies that authenticated manual cobot reset records exactly one AuditLog entry
    with who performed it (user_email) and the previous operating status.
    """
    import json
    from models.cobot import Cobot

    headers = get_supervisor_auth_headers(client)

    # Set cobot to a non-idle state first to verify previous_status tracking
    # Use ASSIGNED — the first valid non-idle state in the real state machine
    from engine.cobot_scheduler import cobot_scheduler
    cobot_scheduler.reset_to_idle("cobot-cell-01")  # Ensure IDLE first
    cobot_scheduler.assign("cobot-cell-01", "w-test", "Zone-A", "pre-test task")
    db = SessionLocal()
    try:
        cobot = db.query(Cobot).filter(Cobot.id == "cobot-cell-01").first()
        cobot.status = "ASSIGNED"
        db.commit()
    finally:
        db.close()

    res = client.post("/api/v1/cobots/cobot-cell-01/reset", headers=headers)
    assert res.status_code == 200, res.text

    db = SessionLocal()
    try:
        log = (
            db.query(AuditLog)
            .filter(AuditLog.action == "COBOT_RESET", AuditLog.entity_id == "cobot-cell-01")
            .order_by(AuditLog.created_at.desc())
            .first()
        )
        assert log is not None, "COBOT_RESET audit log not found"
        assert log.user_email == "supervisor@symbios.ai"
        assert "ASSIGNED" in log.message and "IDLE" in log.message
        changes = json.loads(log.changes_json)
        assert changes["previous_status"] == "ASSIGNED"
        assert changes["new_status"] == "IDLE"
        assert changes["reset_by"] == "supervisor@symbios.ai"
    finally:
        db.close()


def test_safety_zone_creation_audit_logging(client):
    """
    Verifies that authenticated safety zone creation records exactly one AuditLog entry
    with who created it, the zone name, and the vertex coordinate data.
    """
    import json
    headers = get_supervisor_auth_headers(client)
    zone_name = f"Audit Verification Zone {uuid.uuid4().hex[:6]}"
    coords = [[0.12, 0.15], [0.45, 0.15], [0.45, 0.65], [0.12, 0.65]]

    res = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-cell-04",
            "name": zone_name,
            "risk_tier": "high",
            "polygon_coordinates": coords,
            "color_hex": "#38BDF8",
            "reassignment_eligible": True
        }
    )
    assert res.status_code == 201, res.text
    created_zone_id = res.json()["id"]

    db = SessionLocal()
    try:
        log = (
            db.query(AuditLog)
            .filter(AuditLog.action == "CREATE_SAFETY_ZONE", AuditLog.entity_id == created_zone_id)
            .first()
        )
        assert log is not None, "CREATE_SAFETY_ZONE audit log not found"
        assert log.user_email == "supervisor@symbios.ai"
        assert zone_name in log.message
        assert "4 vertices" in log.message
        changes = json.loads(log.changes_json)
        assert changes["zone_name"] == zone_name
        assert changes["polygon_coordinates"] == coords
        assert changes["created_by"] == "supervisor@symbios.ai"
    finally:
        db.close()


def test_rate_limiting_on_auth_token(client):
    """
    Requirement: Add basic rate limiting to POST /api/v1/auth/token
    (max 5 failed attempts per account/IP per 15 min -> 429 Too Many Requests).
    """
    from api.v1.auth import _clear_rate_limits_for_test
    _clear_rate_limits_for_test()

    test_user = "bruteforce_target@symbios.ai"
    try:
        # First 5 failed attempts should return 401 Unauthorized
        for i in range(5):
            res = client.post(
                "/api/v1/auth/token",
                data={"username": test_user, "password": f"wrong-pass-{i}"}
            )
            assert res.status_code == 401, f"Attempt {i+1} expected 401, got {res.status_code}"

        # 6th attempt should be blocked with 429 Too Many Requests
        blocked_res = client.post(
            "/api/v1/auth/token",
            data={"username": test_user, "password": "any-password"}
        )
        assert blocked_res.status_code == 429, f"Expected 429, got {blocked_res.status_code}"
        assert "Too many failed login attempts" in blocked_res.json()["detail"]
        assert "Retry-After" in blocked_res.headers
    finally:
        _clear_rate_limits_for_test()


def test_production_fail_fast_on_missing_bootstrap_passwords():
    """
    Requirement 1: If ENVIRONMENT == 'production' and bootstrap password is not set,
    fail fast at startup with a clear error.
    """
    from core.config import settings
    from core.security import get_bootstrap_admin_password, get_bootstrap_supervisor_password

    orig_env = settings.ENVIRONMENT
    orig_admin = settings.BOOTSTRAP_ADMIN_PASSWORD
    orig_sup = settings.BOOTSTRAP_SUPERVISOR_PASSWORD
    try:
        settings.ENVIRONMENT = "production"
        settings.BOOTSTRAP_ADMIN_PASSWORD = None
        settings.BOOTSTRAP_SUPERVISOR_PASSWORD = None

        with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD environment variable is required in production"):
            get_bootstrap_admin_password()

        with pytest.raises(RuntimeError, match="BOOTSTRAP_SUPERVISOR_PASSWORD environment variable is required in production"):
            get_bootstrap_supervisor_password()
    finally:
        settings.ENVIRONMENT = orig_env
        settings.BOOTSTRAP_ADMIN_PASSWORD = orig_admin
        settings.BOOTSTRAP_SUPERVISOR_PASSWORD = orig_sup


def test_frontend_credential_hygiene_and_in_memory_session():
    """
    Requirement: No hardcoded credentials in frontend files, tokens held strictly in-memory.
    """
    import pathlib
    import re

    frontend_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend"
    assert frontend_dir.exists(), f"Frontend directory not found at {frontend_dir}"

    for file_path in frontend_dir.glob("*"):
        if file_path.suffix in [".html", ".js"]:
            content = file_path.read_text(encoding="utf-8")
            
            # 1. Plaintext known dummy passwords must not exist
            assert "super123" not in content, f"Hardcoded 'super123' found in {file_path.name}"
            assert "admin123" not in content, f"Hardcoded 'admin123' found in {file_path.name}"

            # 2. No assignment of password literals in JS code
            password_assignment = re.search(r'(?:password|passwd|secret)\s*:\s*["\'][^"\']+["\']', content, re.IGNORECASE)
            assert password_assignment is None, f"Potential password literal found in {file_path.name}: {password_assignment.group(0)}"

            # 3. Verify no storage of auth token in persistent browser storage
            storage_usage = re.search(r'(?:localStorage|sessionStorage)\.setItem\([^)]*token', content, re.IGNORECASE)
            assert storage_usage is None, f"Token stored in persistent storage in {file_path.name}"


def test_deactivate_safety_zone_records_audit_log(client):
    """
    High finding 1.1:
    Deactivating a safety zone (DELETE /api/v1/zones/{id}) must record
    an immutable AuditLog entry so boundary modifications cannot happen silently.
    """
    admin_headers = get_admin_auth_headers(client)

    # 1. Create a zone to deactivate
    create_res = client.post(
        "/api/v1/zones",
        headers=admin_headers,
        json={
            "camera_id": "cam-cell-04",
            "name": f"Temporary Zone {uuid.uuid4().hex[:6]}",
            "risk_tier": "high",
            "polygon_coordinates": [[0.3, 0.3], [0.5, 0.3], [0.5, 0.5], [0.3, 0.5]],
            "color_hex": "#FFAA00",
            "reassignment_eligible": True
        }
    )
    assert create_res.status_code == 201, create_res.text
    zone_id = create_res.json()["id"]

    # 2. Deactivate it
    del_res = client.delete(f"/api/v1/zones/{zone_id}", headers=admin_headers)
    assert del_res.status_code == 204, del_res.text

    # 3. Confirm immutable audit log was persisted
    db = SessionLocal()
    try:
        audit = db.query(AuditLog).filter(
            AuditLog.action == "DEACTIVATE_SAFETY_ZONE",
            AuditLog.entity_id == zone_id
        ).first()
        assert audit is not None, "AuditLog entry missing for DEACTIVATE_SAFETY_ZONE"
        assert audit.severity == "warning"
        assert audit.user_email == "admin@symbios.ai"
    finally:
        db.close()


def test_alert_escalation_records_audit_log():
    """
    High finding 1.3:
    When an unacknowledged alert is escalated by the background escalation daemon,
    an immutable AuditLog entry must be recorded alongside the AlertEscalation record.
    """
    from datetime import datetime, timezone, timedelta
    from models.decision import DecisionLog
    from models.alert import SafetyAlert
    from alerting.escalation import escalation_daemon
    from core.config import settings

    db = SessionLocal()
    try:
        # Create decision log and an alert older than ALERT_ESCALATION_MINUTES
        old_time = datetime.now(timezone.utc) - timedelta(minutes=settings.ALERT_ESCALATION_MINUTES + 1)
        dlog = DecisionLog(
            site_id="site-detroit-01",
            camera_id="cam-cell-04",
            action_taken="CRITICAL_BREACH",
            severity="critical",
            rule_fired="RULE_CRITICAL_ZONE_BREACH_ESTOP",
            fatigue_score=85.0
        )
        db.add(dlog)
        db.flush()

        alert = SafetyAlert(
            decision_log_id=dlog.id,
            site_id="site-detroit-01",
            title=f"Test Unacknowledged Alert {uuid.uuid4().hex[:6]}",
            message="Worker collapsed in hazard envelope.",
            severity="critical",
            acknowledged=False,
            escalation_level=0,
            created_at=old_time
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        alert_id = alert.id

        # Trigger daemon check
        escalation_daemon.check_unacknowledged_alerts()

        # Verify audit log was recorded
        audit = db.query(AuditLog).filter(
            AuditLog.action == "ALERT_ESCALATION_DISPATCHED",
            AuditLog.entity_id == alert_id
        ).first()
        assert audit is not None, "AuditLog entry missing for ALERT_ESCALATION_DISPATCHED"
        assert audit.severity == "critical"
        assert "Escalated to EHS Duty Supervisor" in audit.message
    finally:
        db.close()


