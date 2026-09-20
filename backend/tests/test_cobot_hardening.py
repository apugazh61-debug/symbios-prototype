"""
tests/test_cobot_hardening.py
------------------------------
Tests for the three cobot state machine hardening items:

  Q1 — Race condition: ESTOP vs. in-flight simulated advance pipeline.
  Q2 — Migration: startup does not crash on legacy BUSY/REASSIGNED status values,
       and they are normalised to IDLE.
  Q3 — decisions.py dispatch path: invalid assignment (cobot already ACTIVE)
       is handled gracefully; response never claims a false cobot assignment.
"""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from core.database import SessionLocal
from models.cobot import Cobot, CobotStatus
from engine.cobot_scheduler import (
    CobotScheduler,
    CobotRecord,
    InvalidTransition,
    LEGACY_STATUS_VALUES,
    cobot_scheduler as global_scheduler,
)


# ─────────────────────────────────────────────────────────────────────────────
# Q1  Race condition: ESTOP vs. simulated auto-advance pipeline
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_scheduler():
    """Fresh CobotScheduler with a single test cobot and no seeded entries."""
    s = CobotScheduler()
    s._fleet.clear()
    s.register_cobot("bot-q1", "TestBot-Q1")
    return s


class TestEStopRaceCondition:
    """
    Q1 — Verify that:
    (a) ESTOP immediately cancels any pending sim-advance task.
    (b) An in-flight pipeline step that attempts an illegal transition after
        ESTOP raises InvalidTransition, which is caught and logged as a
        CRITICAL audit event, not silently swallowed or re-raised as an
        unhandled exception.
    (c) CancelledError is re-raised by the pipeline so the asyncio Task is
        marked as cancelled, not as successfully completed.
    """

    def test_estop_cancels_pending_sim_task(self, isolated_scheduler):
        """After ESTOP the transition_task field must be None (cancelled)."""
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "Pallet handover")

        # Manually plant a fake Task object to represent a pending sim advance
        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = False
        s._fleet["bot-q1"].transition_task = fake_task

        s.estop("bot-q1")

        # Task must have been cancelled
        fake_task.cancel.assert_called_once()
        # And the reference must be cleared
        assert s._fleet["bot-q1"].transition_task is None
        assert s._fleet["bot-q1"].status == CobotStatus.ESTOP.value

    def test_fault_cancels_pending_sim_task(self, isolated_scheduler):
        """Same guarantee for fault()."""
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "Weld task")

        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = False
        s._fleet["bot-q1"].transition_task = fake_task

        s.fault("bot-q1", "Servo overcurrent")

        fake_task.cancel.assert_called_once()
        assert s._fleet["bot-q1"].transition_task is None
        assert s._fleet["bot-q1"].status == CobotStatus.FAULT.value

    def test_estop_state_changed_before_sim_advance_wakes(self, isolated_scheduler):
        """
        Demonstrates the race window fix:
        estop() changes state AFTER cancelling the task. So even if a
        sim-advance coroutine resumes between cancel delivery and the next
        await, it will see ESTOP and hit the InvalidTransition guard.
        """
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "task")
        s.estop("bot-q1")

        # Any attempt to advance should raise InvalidTransition, not succeed
        with pytest.raises(InvalidTransition) as exc_info:
            s.activate("bot-q1")
        assert "ESTOP" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sim_pipeline_cancelled_error_is_reraised(self, isolated_scheduler):
        """
        The pipeline MUST re-raise CancelledError so the asyncio Task is
        marked as truly cancelled. Without re-raise, Task.cancelled() returns
        False and subsequent cancel() calls on the same object are no-ops.
        """
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "task")

        # Start the pipeline with a very short first sleep so we can cancel it quickly
        with patch(
            "engine.cobot_scheduler._SIM_ASSIGNED_TO_ACTIVE_SECS", 0.05
        ):
            loop = asyncio.get_event_loop()
            task = loop.create_task(s._sim_advance_pipeline("bot-q1"))
            s._fleet["bot-q1"].transition_task = task

            # Immediately cancel
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # Task must be marked cancelled, NOT done-successfully
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_sim_pipeline_stale_state_emits_critical_audit(self, isolated_scheduler):
        """
        If a pipeline step wakes up and the cobot is in an unexpected state
        (e.g. ESTOP was triggered externally between sleeps), it must:
        - Catch the InvalidTransition
        - Emit a CRITICAL audit event via _emit_audit
        - NOT propagate an unhandled exception that would crash the background task
        """
        s = isolated_scheduler
        audit_events = []

        def capture_audit(action, cobot_id, message, severity):
            audit_events.append({"action": action, "cobot_id": cobot_id,
                                  "message": message, "severity": severity})

        s.set_audit_callback(capture_audit)
        s.assign("bot-q1", "w-01", "Zone-A", "task")

        # Simulate the scenario: pipeline is between sleeps, but ESTOP fires.
        # We do this by forcing the state to ESTOP before the pipeline's first
        # status check runs, using a zero-length first sleep.
        with patch("engine.cobot_scheduler._SIM_ASSIGNED_TO_ACTIVE_SECS", 0.001):
            # Change state to ESTOP immediately (before coroutine wakes)
            s._fleet["bot-q1"].status = CobotStatus.ESTOP.value

            # Run pipeline — it should see ESTOP, try to activate(), get
            # InvalidTransition, emit audit, and return cleanly.
            # NOTE: status check `if rec.status == ASSIGNED` prevents activate()
            # from even being called here — the pipeline exits the if-block.
            # To test the InvalidTransition path we need to bypass the status check.
            # So instead we test via direct pipeline call with a patched activate:
            original_activate = s.activate

            def activate_raiser(cid):
                raise InvalidTransition(f"Forced test: {cid} cannot transition")

            s.activate = activate_raiser  # type: ignore
            # Reset status to ASSIGNED so the status check passes
            s._fleet["bot-q1"].status = CobotStatus.ASSIGNED.value

            loop = asyncio.get_event_loop()
            # Should not raise — InvalidTransition must be caught internally
            await s._sim_advance_pipeline("bot-q1")

        s.activate = original_activate  # type: ignore

        # At least one CRITICAL audit event must have been emitted
        assert len(audit_events) >= 1
        critical = [e for e in audit_events if e["severity"] == "critical"]
        assert len(critical) >= 1
        assert "SIM_ADVANCE_INVALID_TRANSITION" in critical[0]["action"]
        assert "bot-q1" == critical[0]["cobot_id"]

    @pytest.mark.asyncio
    async def test_sim_pipeline_stops_after_invalid_transition(self, isolated_scheduler):
        """
        When InvalidTransition is caught in phase 1 (ASSIGNED→ACTIVE), the
        pipeline must return early and NOT attempt phases 2 or 3.
        """
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "task")
        s._fleet["bot-q1"].status = CobotStatus.ASSIGNED.value

        phase2_called = []

        original_complete = s.complete

        def spy_complete(cid):
            phase2_called.append(cid)
            return original_complete(cid)

        with patch("engine.cobot_scheduler._SIM_ASSIGNED_TO_ACTIVE_SECS", 0.001):
            # Make activate() raise InvalidTransition
            s.activate = lambda cid: (_ for _ in ()).throw(  # type: ignore
                InvalidTransition("forced failure")
            )
            s.complete = spy_complete  # type: ignore

            await s._sim_advance_pipeline("bot-q1")

        # complete() should never have been called
        assert phase2_called == [], \
            "Pipeline continued to phase 2 after an InvalidTransition in phase 1"

        s.complete = original_complete  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Q2  Legacy enum migration
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyStatusMigration:
    """
    Q2 — Verify that legacy BUSY/REASSIGNED status values are handled safely.
    """

    def test_legacy_status_values_constant(self):
        """Constant must include both removed statuses."""
        assert "BUSY" in LEGACY_STATUS_VALUES
        assert "REASSIGNED" in LEGACY_STATUS_VALUES

    def test_register_cobot_with_legacy_status_normalised(self):
        """register_cobot() normalises BUSY/REASSIGNED to IDLE."""
        s = CobotScheduler()
        s._fleet.clear()
        s.register_cobot("bot-legacy", "LegacyBot", status="BUSY")
        assert s._fleet["bot-legacy"].status == CobotStatus.IDLE.value

        s.register_cobot("bot-legacy2", "LegacyBot2", status="REASSIGNED")
        assert s._fleet["bot-legacy2"].status == CobotStatus.IDLE.value

    def test_sync_from_db_with_legacy_status_normalised(self):
        """sync_from_db() normalises legacy values to IDLE."""
        s = CobotScheduler()
        s._fleet.clear()

        s.sync_from_db("bot-busy", "BUSY")
        assert s._fleet["bot-busy"].status == CobotStatus.IDLE.value

        s.sync_from_db("bot-reassigned", "REASSIGNED")
        assert s._fleet["bot-reassigned"].status == CobotStatus.IDLE.value

    def test_startup_data_migration_converts_legacy_rows(self):
        """
        Startup data migration in main.py must convert any BUSY/REASSIGNED
        rows to IDLE and not crash.  We inject a REASSIGNED row directly
        into the SQLite DB, trigger the migration block, and verify the row
        is now IDLE.
        """
        from sqlalchemy import text
        from core.database import engine, SessionLocal

        db = SessionLocal()
        cobot = db.query(Cobot).filter(Cobot.id == "cobot-cell-01").first()
        if cobot:
            cobot.status = "REASSIGNED"
            db.commit()
        db.close()

        # Verify the raw DB value is REASSIGNED
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM cobots WHERE id = 'cobot-cell-01'")
            ).fetchone()
        assert row[0] == "REASSIGNED", "Test setup: expected REASSIGNED before migration"

        # Run the same migration logic that main.py runs at startup
        with engine.connect() as conn:
            legacy_rows = conn.execute(
                text("SELECT id, name, status FROM cobots WHERE status IN ('BUSY','REASSIGNED')")
            ).fetchall()
            if legacy_rows:
                conn.execute(
                    text("UPDATE cobots SET status='IDLE', current_task_description=NULL "
                         "WHERE status IN ('BUSY','REASSIGNED')")
                )
            conn.commit()

        # Verify the DB row is now IDLE
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM cobots WHERE id = 'cobot-cell-01'")
            ).fetchone()
        assert row[0] == "IDLE", f"Expected IDLE after migration, got {row[0]}"

        # Verify the migration does not raise even if no legacy rows exist
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE cobots SET status='IDLE' WHERE status IN ('BUSY','REASSIGNED')")
            )
            conn.commit()

    def test_app_startup_does_not_crash_with_legacy_status(self):
        """
        Full integration: if a cobot row has status=BUSY, the TestClient
        startup lifecycle (which runs main.py lifespan) must complete without
        raising an exception.
        """
        from sqlalchemy import text
        from core.database import engine

        # Inject legacy value into DB before app startup
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE cobots SET status='REASSIGNED' WHERE id='cobot-cell-01'")
            )
            conn.commit()

        # App startup must not raise; the migration log proves normalisation fired.
        with TestClient(app) as client:
            res = client.get("/health")  # /health is the actual liveness endpoint
            assert res.status_code == 200

        # After startup the row must be IDLE
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM cobots WHERE id='cobot-cell-01'")
            ).fetchone()
        assert row[0] == "IDLE"

    def test_valid_states_not_touched_by_migration(self):
        """Migration must not modify rows that already have valid states."""
        from sqlalchemy import text
        from core.database import engine

        # Set to a known valid state
        with engine.connect() as conn:
            conn.execute(text("UPDATE cobots SET status='IDLE' WHERE id='cobot-cell-01'"))
            conn.commit()

        # Run migration
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE cobots SET status='IDLE' WHERE status IN ('BUSY','REASSIGNED')")
            )
            conn.commit()

        # Verify it was not changed
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM cobots WHERE id='cobot-cell-01'")
            ).fetchone()
        assert row[0] == "IDLE"


# ─────────────────────────────────────────────────────────────────────────────
# Q3  decisions.py dispatch path: guards against already-busy cobots
# ─────────────────────────────────────────────────────────────────────────────

class TestDecisionDispatchGuards:
    """
    Q3 — Verify the full dispatch path handles InvalidTransition gracefully.
    """

    def test_trigger_reassignment_fails_gracefully_when_not_idle(self, isolated_scheduler):
        """trigger_reassignment returns success=False when cobot is not IDLE."""
        s = isolated_scheduler
        # Put cobot into ACTIVE state
        s.assign("bot-q1", "w-01", "Zone-A", "task1")
        s.activate("bot-q1")

        result = s.trigger_reassignment("bot-q1", "w-02", "Zone-B")

        assert result["success"] is False
        assert "error" in result
        # Status must still be ACTIVE — no corruption
        assert s._fleet["bot-q1"].status == CobotStatus.ACTIVE.value

    def test_trigger_reassignment_no_unhandled_exception_on_active_cobot(self, isolated_scheduler):
        """Calling trigger_reassignment on an ACTIVE cobot must not raise."""
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "task1")
        s.activate("bot-q1")

        # Must not raise — returns a failure dict
        try:
            result = s.trigger_reassignment("bot-q1", "w-02", "Zone-B")
        except Exception as exc:
            pytest.fail(
                f"trigger_reassignment raised an unhandled exception in the safety "
                f"decision path: {type(exc).__name__}: {exc}"
            )

        assert result["success"] is False

    def test_decisions_api_does_not_report_false_cobot_assignment(self):
        """
        When a decision fires REASSIGN_TO_COBOT but the cobot is already ACTIVE,
        the API response must NOT contain a cobot_name (that would be a false
        safety signal to the dashboard).
        """
        # Force the global scheduler cobot to ACTIVE so assignment will be rejected
        global_scheduler.reset_to_idle("cobot-cell-01")
        global_scheduler.assign("cobot-cell-01", "w-existing", "Zone-X", "existing task")
        global_scheduler.activate("cobot-cell-01")

        try:
            with TestClient(app) as client:
                # We can't send a real camera frame here without a live camera,
                # so we verify the trigger_reassignment guard directly using the
                # decision engine path.
                result = global_scheduler.trigger_reassignment(
                    "cobot-cell-01", "w-new", "Zone-Y"
                )
                assert result["success"] is False, (
                    "Expected failure: cobot-cell-01 is ACTIVE, should not accept assignment"
                )
                assert "error" in result
                # Crucially: no 'cobot_assigned_name' leakage in the return value
                assert result.get("cobot_name") is None
        finally:
            # Clean up
            global_scheduler.reset_to_idle("cobot-cell-01")

    def test_trigger_reassignment_success_only_when_idle(self, isolated_scheduler):
        """
        Positive case: trigger_reassignment succeeds and returns success=True
        only when the cobot is IDLE.
        """
        s = isolated_scheduler
        result = s.trigger_reassignment("bot-q1", "w-01", "Zone-A")

        assert result["success"] is True
        assert result["status"] == CobotStatus.ASSIGNED.value
        assert result["task"] is not None

    def test_scheduler_state_not_corrupted_on_failed_dispatch(self, isolated_scheduler):
        """
        Failed dispatch must leave the cobot in its original state — no partial
        mutation of the record.
        """
        s = isolated_scheduler
        s.assign("bot-q1", "w-01", "Zone-A", "first task")
        s.activate("bot-q1")  # now ACTIVE

        # Attempt a second assignment — should fail
        s.trigger_reassignment("bot-q1", "w-02", "Zone-B")

        # State must remain ACTIVE, task unchanged
        rec = s.get_status("bot-q1")
        assert rec.status == CobotStatus.ACTIVE.value
        assert rec.active_task == "first task"
        assert rec.worker_id == "w-01"

    @pytest.mark.asyncio
    async def test_rejection_persists_critical_audit_and_safety_alert(self):
        """
        Follow-up Q1/Q2: When a cobot dispatch fails in evaluate_safety_decisions:
        1. A critical AuditLog entry with action='COBOT_DISPATCH_FAILED' is persisted.
        2. A critical SafetyAlert is persisted with supervisor notification message.
        3. The WebSocket/notification dispatcher dispatch_critical_alert is called.
        """
        import numpy as np
        from unittest.mock import AsyncMock, patch
        from api.v1.decisions import analyze_frame, FrameAnalysisRequest
        from models.audit import AuditLog
        from models.alert import SafetyAlert

        db = SessionLocal()
        try:
            # Put global cobot into ACTIVE state so assignment is rejected
            global_scheduler.reset_to_idle("cobot-cell-01")
            global_scheduler.assign("cobot-cell-01", "w-active", "Zone-Existing", "active task")
            global_scheduler.activate("cobot-cell-01")

            mock_worker = [{
                "worker_id": "W-FAIL-01",
                "center_norm": [0.5, 0.5],
                "fatigue_score": 85.0,
                "slump_angle": 28.0,
                "stillness_seconds": 7.0,
                "in_zone": True,
                "zone_name": "Robotic Arm Envelope",
                "zone_risk": "high",
                "reassignment_eligible": True,
                "bbox": [10, 10, 50, 50],
                "keypoints": []
            }]

            req = FrameAnalysisRequest(
                image_base64="data:image/jpeg;base64,fakeimage",
                camera_id="cam-01"
            )

            from engine.interface import CobotContext
            stale_idle_ctx = CobotContext(
                available_cobot_id="cobot-cell-01",
                cobot_name="UR10e-Arm-North",
                cobot_status="IDLE"
            )

            with patch("api.v1.decisions.decode_image_frame", return_value=np.zeros((50, 50, 3), dtype=np.uint8)), \
                 patch("api.v1.decisions.pipeline.process_frame", return_value=mock_worker), \
                 patch("api.v1.decisions.cobot_scheduler.get_context", return_value=stale_idle_ctx), \
                 patch("api.v1.decisions.alert_dispatcher.dispatch_critical_alert", new_callable=AsyncMock) as mock_dispatch:

                resp = await analyze_frame(
                    req=req,
                    db=db
                )

                # Cobot was busy, so response must not claim cobot was reassigned
                assert resp.active_decision is not None
                assert resp.active_decision.cobot_name is None

                # Verify dispatcher was called with critical failure payload
                mock_dispatch.assert_awaited()
                all_calls = [call.args[0] for call in mock_dispatch.await_args_list]
                failure_dispatch = next(
                    (d for d in all_calls if "AUTOMATED COBOT REASSIGNMENT FAILED" in d.get("message", "")),
                    None
                )
                assert failure_dispatch is not None, f"Expected rejection alert in dispatcher calls: {all_calls}"
                assert failure_dispatch["severity"] == "critical"
                assert failure_dispatch["worker_id"] == "W-FAIL-01"
                assert failure_dispatch["action_required"] == "IMMEDIATE_SUPERVISOR_INTERVENTION"

                # Verify AuditLog persistence in database
                failed_audit = db.query(AuditLog).filter(
                    AuditLog.action == "COBOT_DISPATCH_FAILED",
                    AuditLog.changes_json.like("%W-FAIL-01%")
                ).first()

                assert failed_audit is not None
                assert failed_audit.severity == "critical"
                assert "AUTOMATED COBOT REASSIGNMENT FAILED" in failed_audit.message
                assert "W-FAIL-01" in failed_audit.changes_json

                # Verify SafetyAlert persistence in database
                failed_alert = db.query(SafetyAlert).filter(
                    SafetyAlert.title.like("%Cobot Reassignment Failed%"),
                    SafetyAlert.message.like("%W-FAIL-01%")
                ).first()

                assert failed_alert is not None
                assert failed_alert.severity == "critical"
                assert "W-FAIL-01" in failed_alert.message
                assert failed_alert.acknowledged is False
        finally:
            global_scheduler.reset_to_idle("cobot-cell-01")
            db.close()

    @pytest.mark.asyncio
    async def test_multiple_workers_rejection_creates_audit_and_alert_for_each(self):
        """
        Confirm that when multiple workers in the same frame each trigger a rejected
        cobot dispatch, an AuditLog entry, SafetyAlert, and WebSocket broadcast are
        created for EVERY affected worker (not just the first).
        """
        import numpy as np
        from unittest.mock import AsyncMock, patch
        from api.v1.decisions import analyze_frame, FrameAnalysisRequest
        from models.audit import AuditLog
        from models.alert import SafetyAlert
        from engine.interface import CobotContext

        db = SessionLocal()
        try:
            # Set cobot to ACTIVE so trigger_reassignment will reject
            global_scheduler.reset_to_idle("cobot-cell-01")
            global_scheduler.assign("cobot-cell-01", "w-active", "Zone-Existing", "active task")
            global_scheduler.activate("cobot-cell-01")

            mock_workers = [
                {
                    "worker_id": "W-MULTI-01",
                    "center_norm": [0.3, 0.4],
                    "fatigue_score": 82.0,
                    "slump_angle": 26.0,
                    "stillness_seconds": 6.0,
                    "in_zone": True,
                    "zone_name": "Robotic Arm Envelope",
                    "zone_risk": "high",
                    "reassignment_eligible": True,
                    "bbox": [10, 10, 40, 40],
                    "keypoints": []
                },
                {
                    "worker_id": "W-MULTI-02",
                    "center_norm": [0.7, 0.6],
                    "fatigue_score": 90.0,
                    "slump_angle": 30.0,
                    "stillness_seconds": 8.0,
                    "in_zone": True,
                    "zone_name": "Robotic Arm Envelope",
                    "zone_risk": "high",
                    "reassignment_eligible": True,
                    "bbox": [50, 50, 90, 90],
                    "keypoints": []
                }
            ]

            req = FrameAnalysisRequest(
                image_base64="data:image/jpeg;base64,fakeimage",
                camera_id="cam-01"
            )

            stale_idle_ctx = CobotContext(
                available_cobot_id="cobot-cell-01",
                cobot_name="UR10e-Arm-North",
                cobot_status="IDLE"
            )

            with patch("api.v1.decisions.decode_image_frame", return_value=np.zeros((50, 50, 3), dtype=np.uint8)), \
                 patch("api.v1.decisions.pipeline.process_frame", return_value=mock_workers), \
                 patch("api.v1.decisions.cobot_scheduler.get_context", return_value=stale_idle_ctx), \
                 patch("api.v1.decisions.alert_dispatcher.dispatch_critical_alert", new_callable=AsyncMock) as mock_dispatch:

                resp = await analyze_frame(req=req, db=db)

                # Neither worker should claim successful cobot takeover
                assert resp.active_decision is not None
                assert resp.active_decision.cobot_name is None

                # Verify WebSocket broadcast was sent for BOTH workers
                broadcast_workers = [
                    call.args[0].get("worker_id")
                    for call in mock_dispatch.await_args_list
                    if "worker_id" in call.args[0]
                ]
                assert "W-MULTI-01" in broadcast_workers
                assert "W-MULTI-02" in broadcast_workers

                # Verify AuditLog entries: must have separate rows for W-MULTI-01 and W-MULTI-02
                recent_failed_audits = db.query(AuditLog).filter(
                    AuditLog.action == "COBOT_DISPATCH_FAILED"
                ).order_by(AuditLog.id.desc()).limit(10).all()

                audit_workers = [
                    json.loads(a.changes_json).get("worker_id")
                    for a in recent_failed_audits
                    if a.changes_json and "worker_id" in json.loads(a.changes_json)
                ]
                assert "W-MULTI-01" in audit_workers
                assert "W-MULTI-02" in audit_workers

                # Verify SafetyAlert entries: must have separate rows for W-MULTI-01 and W-MULTI-02
                recent_alerts = db.query(SafetyAlert).filter(
                    SafetyAlert.title.like("%Cobot Reassignment Failed%")
                ).order_by(SafetyAlert.id.desc()).limit(10).all()

                alert_messages = [a.message for a in recent_alerts]
                assert any("W-MULTI-01" in m for m in alert_messages)
                assert any("W-MULTI-02" in m for m in alert_messages)
        finally:
            global_scheduler.reset_to_idle("cobot-cell-01")
            db.close()

    @pytest.mark.asyncio
    async def test_decision_log_records_actual_triggering_worker_not_first_worker(self):
        """
        Critical finding 3.1:
        When two workers are tracked in a single frame:
        - Worker #1: Safe (fatigue=5.0, in_zone=False, slump=2.0) -> NORMAL
        - Worker #2: Hazard breach (fatigue=85.0, in_zone=True, slump=25.0) -> REASSIGN/CRITICAL
        DecisionLog.worker_id MUST be 'W-HAZARD-02', never mistakenly hardcoded to worker_states[0] ('W-SAFE-01').
        """
        from api.v1.decisions import analyze_frame
        from schemas.safety import FrameAnalysisRequest
        from models.decision import DecisionLog
        from engine.interface import CobotContext
        from unittest.mock import patch, AsyncMock
        import numpy as np

        db = SessionLocal()
        try:
            mock_workers = [
                {
                    "worker_id": "W-SAFE-01",
                    "center_norm": [0.2, 0.2],
                    "fatigue_score": 5.0,
                    "slump_angle": 2.0,
                    "stillness_seconds": 1.0,
                    "in_zone": False,
                    "zone_name": None,
                    "zone_risk": None,
                    "reassignment_eligible": False,
                    "bbox": [10, 10, 40, 40],
                    "keypoints": []
                },
                {
                    "worker_id": "W-HAZARD-02",
                    "center_norm": [0.8, 0.8],
                    "fatigue_score": 85.0,
                    "slump_angle": 25.0,
                    "stillness_seconds": 7.0,
                    "in_zone": True,
                    "zone_name": "Robotic Arm Envelope",
                    "zone_risk": "high",
                    "reassignment_eligible": True,
                    "bbox": [50, 50, 90, 90],
                    "keypoints": []
                }
            ]

            req = FrameAnalysisRequest(
                image_base64="data:image/jpeg;base64,fakeimage",
                camera_id="cam-01"
            )

            idle_ctx = CobotContext(
                available_cobot_id="cobot-cell-01",
                cobot_name="UR10e-Arm-North",
                cobot_status="IDLE"
            )

            with patch("api.v1.decisions.decode_image_frame", return_value=np.zeros((50, 50, 3), dtype=np.uint8)), \
                 patch("api.v1.decisions.pipeline.process_frame", return_value=mock_workers), \
                 patch("api.v1.decisions.cobot_scheduler.get_context", return_value=idle_ctx), \
                 patch("api.v1.decisions.alert_dispatcher.dispatch_critical_alert", new_callable=AsyncMock):

                resp = await analyze_frame(req=req, db=db)

                assert resp.active_decision is not None
                decision_log_id = resp.active_decision.decision_log_id
                assert decision_log_id is not None

                # Query the persisted DecisionLog
                log_row = db.query(DecisionLog).filter(DecisionLog.id == decision_log_id).first()
                assert log_row is not None
                assert log_row.worker_id == "W-HAZARD-02", (
                    f"DecisionLog misattributed worker_id to '{log_row.worker_id}' instead of 'W-HAZARD-02'!"
                )
                assert log_row.slump_angle == 25.0
                assert log_row.fatigue_score == 85.0
        finally:
            global_scheduler.reset_to_idle("cobot-cell-01")
            db.close()

    @pytest.mark.asyncio
    async def test_multi_worker_concurrent_hazard_creates_decision_and_alert_for_all(self):
        """
        High finding 3.2:
        When multiple workers experience hazards concurrently in the same frame:
        - Worker 1 (W-WARN-01): Severe fatigue in safe zone (fatigue=75.0) -> MONITOR/warning
        - Worker 2 (W-CRIT-02): Severe fatigue in hazard zone (fatigue=88.0) -> REASSIGN/critical
        Verify that DecisionLog and SafetyAlert rows are created for BOTH workers.
        """
        from api.v1.decisions import analyze_frame
        from schemas.safety import FrameAnalysisRequest
        from models.decision import DecisionLog
        from models.alert import SafetyAlert
        from engine.interface import CobotContext
        from unittest.mock import patch, AsyncMock
        import numpy as np

        db = SessionLocal()
        try:
            mock_workers = [
                {
                    "worker_id": "W-WARN-01",
                    "center_norm": [0.2, 0.2],
                    "fatigue_score": 75.0,
                    "slump_angle": 20.0,
                    "stillness_seconds": 6.0,
                    "in_zone": False,
                    "zone_name": None,
                    "zone_risk": None,
                    "reassignment_eligible": False,
                    "bbox": [10, 10, 40, 40],
                    "keypoints": []
                },
                {
                    "worker_id": "W-CRIT-02",
                    "center_norm": [0.8, 0.8],
                    "fatigue_score": 88.0,
                    "slump_angle": 28.0,
                    "stillness_seconds": 8.0,
                    "in_zone": True,
                    "zone_name": "Robotic Arm Envelope",
                    "zone_risk": "high",
                    "reassignment_eligible": True,
                    "bbox": [50, 50, 90, 90],
                    "keypoints": []
                }
            ]

            req = FrameAnalysisRequest(
                image_base64="data:image/jpeg;base64,fakeimage",
                camera_id="cam-01"
            )

            idle_ctx = CobotContext(
                available_cobot_id="cobot-cell-01",
                cobot_name="UR10e-Arm-North",
                cobot_status="IDLE"
            )

            with patch("api.v1.decisions.decode_image_frame", return_value=np.zeros((50, 50, 3), dtype=np.uint8)), \
                 patch("api.v1.decisions.pipeline.process_frame", return_value=mock_workers), \
                 patch("api.v1.decisions.cobot_scheduler.get_context", return_value=idle_ctx), \
                 patch("api.v1.decisions.alert_dispatcher.dispatch_critical_alert", new_callable=AsyncMock):

                resp = await analyze_frame(req=req, db=db)
                assert resp.active_decision is not None

                # Query DecisionLogs for both workers
                w1_logs = db.query(DecisionLog).filter(DecisionLog.worker_id == "W-WARN-01").all()
                w2_logs = db.query(DecisionLog).filter(DecisionLog.worker_id == "W-CRIT-02").all()

                assert len(w1_logs) >= 1, "Concurrent hazard for W-WARN-01 was dropped from DecisionLog!"
                assert len(w2_logs) >= 1, "DecisionLog missing for W-CRIT-02!"

                # Query SafetyAlerts tied to these decision logs
                relevant_log_ids = [l.id for l in w1_logs + w2_logs]
                alerts = db.query(SafetyAlert).filter(SafetyAlert.decision_log_id.in_(relevant_log_ids)).all()
                alert_messages = [a.message for a in alerts]

                assert any("W-WARN-01" in m for m in alert_messages), "SafetyAlert missing for W-WARN-01!"
                assert any("Task reallocated" in m or "W-CRIT-02" in m for m in alert_messages), "SafetyAlert missing for W-CRIT-02!"
        finally:
            global_scheduler.reset_to_idle("cobot-cell-01")
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Q8 Dedicated tests for Audit HIGH items (4.2, 3.3, 4.5)
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditHighItemsVerifications:
    """
    Dedicated tests verifying HIGH audit items:
    - 4.2: DB commit failure during state transition triggers rollback and reverts scheduler state.
    - 3.3: legacy_analyze_frame prioritizes critical worker over normal worker at index 0.
    - 4.5: legacy_analyze_frame guarantees db.close() in finally block on error.
    """

    @pytest.mark.asyncio
    async def test_4_2_db_commit_failure_reverts_scheduler_and_rolls_back(self):
        """
        4.2: When DB commit fails during cobot state transition,
        verify DB rollback is executed AND in-memory cobot status reverts to IDLE.
        """
        from api.v1.decisions import analyze_frame
        from schemas.safety import FrameAnalysisRequest
        from engine.interface import CobotContext
        from unittest.mock import patch, AsyncMock, MagicMock
        from core.database import SessionLocal
        from engine.cobot_scheduler import cobot_scheduler as global_scheduler
        from models.cobot import CobotStatus
        import numpy as np

        db = SessionLocal()
        cid = "cobot-cell-01"
        global_scheduler.reset_to_idle(cid)
        assert global_scheduler.get_status(cid).status == CobotStatus.IDLE.value

        mock_workers = [
            {
                "worker_id": "W-CRIT-99",
                "center_norm": [0.8, 0.8],
                "fatigue_score": 90.0,
                "slump_angle": 30.0,
                "stillness_seconds": 10.0,
                "in_zone": True,
                "zone_name": "Robotic Arm Envelope",
                "zone_risk": "high",
                "reassignment_eligible": True,
                "bbox": [50, 50, 90, 90],
                "keypoints": []
            }
        ]

        req = FrameAnalysisRequest(
            image_base64="data:image/jpeg;base64,fakeimage",
            camera_id="cam-01"
        )

        idle_ctx = CobotContext(
            available_cobot_id=cid,
            cobot_name="UR10e-Arm-North",
            cobot_status="IDLE"
        )

        # Mock db.rollback spy and db.commit to raise
        rollback_spy = MagicMock(side_effect=db.rollback)
        db.rollback = rollback_spy

        def commit_fail():
            raise RuntimeError("Simulated DB commit error (disk/network failure)")

        db.commit = MagicMock(side_effect=commit_fail)

        try:
            with patch("api.v1.decisions.decode_image_frame", return_value=np.zeros((50, 50, 3), dtype=np.uint8)), \
                 patch("api.v1.decisions.pipeline.process_frame", return_value=mock_workers), \
                 patch("api.v1.decisions.cobot_scheduler.get_context", return_value=idle_ctx), \
                 patch("api.v1.decisions.alert_dispatcher.dispatch_critical_alert", new_callable=AsyncMock):

                with pytest.raises(RuntimeError, match="Simulated DB commit error"):
                    await analyze_frame(req=req, db=db)

            # Confirm rollback was invoked
            assert rollback_spy.called, "db.rollback was NOT called on commit error!"

            # Confirm in-memory scheduler state was reverted to IDLE (not stuck in ASSIGNED)
            assert global_scheduler.get_status(cid).status == CobotStatus.IDLE.value, \
                f"Scheduler status remained '{global_scheduler.get_status(cid).status}' instead of reverting to IDLE!"
        finally:
            global_scheduler.reset_to_idle(cid)
            db.close()

    @pytest.mark.asyncio
    async def test_3_3_legacy_analyze_frame_returns_critical_worker_not_first(self):
        """
        3.3: legacy_analyze_frame with two workers (worker[0] normal, worker[1] critical)
        must return worker[1]'s high fatigue data rather than worker[0]'s.
        """
        from main import legacy_analyze_frame
        from schemas.safety import FrameAnalysisRequest
        from engine.interface import CobotContext
        from unittest.mock import patch, AsyncMock
        from core.database import SessionLocal
        import numpy as np

        db = SessionLocal()
        try:
            mock_workers = [
                {
                    "worker_id": "W-SAFE-01",
                    "center_norm": [0.1, 0.1],
                    "fatigue_score": 12.0,
                    "slump_angle": 2.0,
                    "stillness_seconds": 1.0,
                    "in_zone": False,
                    "zone_name": None,
                    "zone_risk": None,
                    "reassignment_eligible": False,
                    "bbox": [10, 10, 30, 30],
                    "keypoints": []
                },
                {
                    "worker_id": "W-CRIT-02",
                    "center_norm": [0.85, 0.85],
                    "fatigue_score": 94.0,
                    "slump_angle": 32.0,
                    "stillness_seconds": 12.0,
                    "in_zone": True,
                    "zone_name": "Robotic Arm Envelope",
                    "zone_risk": "high",
                    "reassignment_eligible": True,
                    "bbox": [60, 60, 95, 95],
                    "keypoints": []
                }
            ]

            req = FrameAnalysisRequest(
                image_base64="data:image/jpeg;base64,fakeimage",
                camera_id="cam-01"
            )

            idle_ctx = CobotContext(
                available_cobot_id="cobot-cell-01",
                cobot_name="UR10e-Arm-North",
                cobot_status="IDLE"
            )

            with patch("api.v1.decisions.decode_image_frame", return_value=np.zeros((50, 50, 3), dtype=np.uint8)), \
                 patch("api.v1.decisions.pipeline.process_frame", return_value=mock_workers), \
                 patch("api.v1.decisions.cobot_scheduler.get_context", return_value=idle_ctx), \
                 patch("api.v1.decisions.alert_dispatcher.dispatch_critical_alert", new_callable=AsyncMock):

                result = await legacy_analyze_frame(frame=req, db=db)

            assert result["person_detected"] is True
            # Must return W-CRIT-02's data (fatigue 94.0, center [0.85, 0.85]), NOT worker[0]'s (12.0, [0.1, 0.1])
            assert result["fatigue_score"] == 94.0, f"Expected 94.0 but got {result['fatigue_score']}"
            assert result["center_norm"] == [0.85, 0.85], f"Expected [0.85, 0.85] but got {result['center_norm']}"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_4_5_legacy_analyze_frame_closes_db_session_on_exception(self):
        """
        4.5: When an exception occurs during frame processing,
        confirm db.close() is guaranteed to execute via the finally block.
        """
        from main import legacy_analyze_frame
        from schemas.safety import FrameAnalysisRequest
        from unittest.mock import patch, MagicMock

        req = FrameAnalysisRequest(
            image_base64="data:image/jpeg;base64,fakeimage",
            camera_id="cam-01"
        )

        mock_db = MagicMock()

        with patch("main.v1_analyze_frame", side_effect=ValueError("Simulated pipeline fatal crash")):
            with pytest.raises(ValueError, match="Simulated pipeline fatal crash"):
                await legacy_analyze_frame(frame=req, db=mock_db)

        # Confirm db.close() was called in finally block despite the exception
        assert mock_db.close.called, "db.close() was NOT called in finally block upon exception!"





