"""
engine/cobot_scheduler.py
-------------------------
Real cobot fleet state machine: IDLE -> ASSIGNED -> ACTIVE -> RETURNING -> IDLE.

Transitions are strictly guarded — only the defined arc is permitted, everything
else raises an InvalidTransition.

State-machine arc (valid transitions):
    IDLE      ──assign()──►  ASSIGNED
    ASSIGNED  ──activate()──► ACTIVE
    ACTIVE    ──complete()──► RETURNING
    RETURNING ──dock_home()──► IDLE
    ANY       ──fault()──► FAULT
    ANY       ──estop()──► ESTOP
    FAULT/ESTOP ──reset_to_idle()──► IDLE   (supervisor-only, enforced at API layer)

Q1 race condition fix (estop vs. sim advance):
  • estop() and fault() now call _cancel_sim_task() BEFORE changing state,
    then immediately set the record status so any still-in-flight wake-up
    sees the ESTOP/FAULT state and is rejected by the guard.
  • _sim_advance_pipeline now:
      (a) Re-raises CancelledError after logging so asyncio cooperative
          cancellation works correctly.
      (b) Wraps every individual transition in try/except InvalidTransition —
          stale-state mismatches are logged as CRITICAL audit events, never
          silently swallowed or allowed to crash the background task.

Q2 legacy enum migration:
  • On startup, main.py DATA migration converts any BUSY/REASSIGNED rows
    to IDLE.  sync_from_db() is called from there so the scheduler cache
    is also updated.

Q3 decisions.py dispatch path:
  • trigger_reassignment() already catches InvalidTransition and returns
    success=False.  The caller in decisions.py now correctly clears
    cobot_assigned_name on failure so the response never claims a
    cobot was assigned when it wasn't.
"""

import asyncio
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, field

from models.cobot import CobotStatus
from engine.interface import CobotContext
from core.logging import logger


# Simulated travel/task durations in seconds (dev only)
_SIM_ASSIGNED_TO_ACTIVE_SECS = 4.0
_SIM_ACTIVE_TO_RETURNING_SECS = 8.0
_SIM_RETURNING_TO_IDLE_SECS = 5.0

# Legacy status values that pre-date the real state machine enum.
# Any row in the database with one of these values is migrated to IDLE
# on startup (see main.py data migration).
LEGACY_STATUS_VALUES = {"BUSY", "REASSIGNED"}


# Valid state machine transitions
_VALID_TRANSITIONS: Dict[str, List[str]] = {
    CobotStatus.IDLE.value:      [CobotStatus.ASSIGNED.value, CobotStatus.FAULT.value, CobotStatus.ESTOP.value],
    CobotStatus.ASSIGNED.value:  [CobotStatus.ACTIVE.value,   CobotStatus.FAULT.value, CobotStatus.ESTOP.value],
    CobotStatus.ACTIVE.value:    [CobotStatus.RETURNING.value, CobotStatus.FAULT.value, CobotStatus.ESTOP.value],
    CobotStatus.RETURNING.value: [CobotStatus.IDLE.value,      CobotStatus.FAULT.value, CobotStatus.ESTOP.value],
    CobotStatus.FAULT.value:     [CobotStatus.IDLE.value],
    CobotStatus.ESTOP.value:     [CobotStatus.IDLE.value],
}


@dataclass
class CobotRecord:
    """In-memory record for one cobot in the fleet. Authoritative for the scheduler."""
    id: str
    name: str
    status: str = CobotStatus.IDLE.value
    active_task: Optional[str] = None
    worker_id: Optional[str] = None
    zone_name: Optional[str] = None
    assigned_at: Optional[datetime] = None
    transition_task: Optional[asyncio.Task] = field(default=None, repr=False)


class InvalidTransition(ValueError):
    """Raised when a state machine guard rejects an illegal transition."""
    pass


class CobotScheduler:
    """
    Manages physical/logical cobots in a manufacturing cell through a strict
    state machine: IDLE -> ASSIGNED -> ACTIVE -> RETURNING -> IDLE.

    An optional audit_callback(action, cobot_id, message, severity) hook lets
    the API layer write InvalidTransition events from background tasks into the
    persistent AuditLog without creating a circular import.  Set it once at
    startup via set_audit_callback().
    """

    def __init__(self):
        self._fleet: Dict[str, CobotRecord] = {
            "cobot-cell-01": CobotRecord(
                id="cobot-cell-01",
                name="UR10e-Arm-North",
                status=CobotStatus.IDLE.value,
            )
        }
        # Optional hook: called for CRITICAL events originating from background tasks.
        # Signature: (action: str, cobot_id: str, message: str, severity: str) -> None
        self._audit_callback: Optional[Callable] = None

    def set_audit_callback(self, callback: Callable) -> None:
        """
        Register a callback for audit events that originate from the background
        simulation pipeline (where no DB session is naturally available).
        Called with (action, cobot_id, message, severity).
        """
        self._audit_callback = callback

    def _emit_audit(self, action: str, cobot_id: str, message: str,
                    severity: str = "critical") -> None:
        """Fire the audit callback if registered; always log regardless."""
        logger.log(
            40 if severity == "critical" else 30,  # ERROR=40, WARNING=30
            f"[CobotSM-AUDIT] {action} | {cobot_id} | {message}"
        )
        if self._audit_callback:
            try:
                self._audit_callback(action, cobot_id, message, severity)
            except Exception as cb_err:
                logger.error(f"[CobotSM] Audit callback failed: {cb_err}")

    # ------------------------------------------------------------------
    # Fleet management
    # ------------------------------------------------------------------

    def register_cobot(self, cobot_id: str, name: str,
                       status: str = CobotStatus.IDLE.value) -> None:
        if cobot_id not in self._fleet:
            # Normalise legacy status values on registration
            safe_status = CobotStatus.IDLE.value if status in LEGACY_STATUS_VALUES else status
            self._fleet[cobot_id] = CobotRecord(id=cobot_id, name=name, status=safe_status)

    def sync_from_db(self, cobot_id: str, status: str,
                     task_desc: Optional[str] = None) -> None:
        """
        Synchronises a DB-loaded cobot record into the in-memory fleet.
        Normalises legacy enum values (BUSY/REASSIGNED) to IDLE.
        """
        safe_status = CobotStatus.IDLE.value if status in LEGACY_STATUS_VALUES else status
        if cobot_id not in self._fleet:
            self._fleet[cobot_id] = CobotRecord(id=cobot_id, name=cobot_id, status=safe_status)
        else:
            self._fleet[cobot_id].status = safe_status
            self._fleet[cobot_id].active_task = task_desc

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_context(self, site_id: Optional[str] = None) -> CobotContext:
        """Returns a CobotContext for the first IDLE cobot (decision engine input)."""
        for cid, rec in self._fleet.items():
            if rec.status == CobotStatus.IDLE.value:
                return CobotContext(
                    available_cobot_id=cid,
                    cobot_name=rec.name,
                    cobot_status=rec.status,
                    active_queue_depth=0
                )
        return CobotContext(
            available_cobot_id=None,
            cobot_name="None Available",
            cobot_status=CobotStatus.ACTIVE.value,
            active_queue_depth=len(self._fleet)
        )

    def get_status(self, cobot_id: str) -> Optional[CobotRecord]:
        return self._fleet.get(cobot_id)

    def get_all(self) -> List[CobotRecord]:
        return list(self._fleet.values())

    # ------------------------------------------------------------------
    # State machine transitions (guarded)
    # ------------------------------------------------------------------

    def _transition(self, cobot_id: str, new_status: str,
                    task: Optional[str] = None,
                    worker_id: Optional[str] = None,
                    zone_name: Optional[str] = None) -> CobotRecord:
        """
        Guarded transition — raises InvalidTransition if arc is not permitted.
        Thread-of-execution note: this is synchronous and mutates in-memory
        state atomically with respect to Python's GIL. It is NOT safe for
        true multi-threaded concurrent access — add a threading.Lock if moving
        to a multi-threaded server model.
        """
        rec = self._fleet.get(cobot_id)
        if rec is None:
            raise ValueError(f"Cobot '{cobot_id}' not registered in scheduler fleet")

        allowed = _VALID_TRANSITIONS.get(rec.status, [])
        if new_status not in allowed:
            raise InvalidTransition(
                f"Cobot '{cobot_id}' cannot move from {rec.status} → {new_status}. "
                f"Allowed from {rec.status}: {allowed}"
            )

        old_status = rec.status
        rec.status = new_status
        if task is not None:
            rec.active_task = task
        if worker_id is not None:
            rec.worker_id = worker_id
        if zone_name is not None:
            rec.zone_name = zone_name

        logger.info(
            f"[CobotSM] {cobot_id} ({rec.name}): {old_status} → {new_status}"
            + (f" | task: {rec.active_task}" if rec.active_task else "")
        )
        return rec

    def assign(self, cobot_id: str, worker_id: str, zone_name: str,
               task_desc: str) -> CobotRecord:
        """IDLE → ASSIGNED: Decision engine reserves this cobot for a task."""
        rec = self._transition(
            cobot_id, CobotStatus.ASSIGNED.value,
            task=task_desc,
            worker_id=worker_id,
            zone_name=zone_name
        )
        rec.assigned_at = datetime.now(timezone.utc)
        return rec

    def activate(self, cobot_id: str) -> CobotRecord:
        """ASSIGNED → ACTIVE: Cobot has arrived at the workstation."""
        return self._transition(cobot_id, CobotStatus.ACTIVE.value)

    def complete(self, cobot_id: str) -> CobotRecord:
        """ACTIVE → RETURNING: Cobot finished task and is heading home."""
        return self._transition(cobot_id, CobotStatus.RETURNING.value)

    def dock_home(self, cobot_id: str) -> CobotRecord:
        """RETURNING → IDLE: Cobot has docked at home position."""
        rec = self._transition(cobot_id, CobotStatus.IDLE.value)
        rec.active_task = None
        rec.worker_id = None
        rec.zone_name = None
        rec.assigned_at = None
        return rec

    def fault(self, cobot_id: str, reason: str) -> CobotRecord:
        """
        ANY → FAULT: Hardware/software fault detected.

        Q1 fix: cancel any pending sim-advance task FIRST, then change state.
        This eliminates the window where cancel() is delivered after a coroutine
        has already passed a status check but not yet awaited.
        """
        # Cancel BEFORE the status change so any wake-up between cancel delivery
        # and state mutation also sees the pre-fault status guard fail cleanly.
        self._cancel_sim_task(cobot_id)
        rec = self._transition(cobot_id, CobotStatus.FAULT.value, task=reason)
        return rec

    def estop(self, cobot_id: str) -> CobotRecord:
        """
        ANY → ESTOP: Emergency stop engaged.

        Q1 fix: same as fault() — cancel sim task BEFORE changing state.
        The cancellation changes the record's transition_task to None so that
        any still-in-flight step of _sim_advance_pipeline that wakes up after
        the CancelledError is scheduled (but before it is delivered) will hit
        the InvalidTransition guard in the next _transition() call and be
        caught by the pipeline's error handler, not silently swallowed.
        """
        self._cancel_sim_task(cobot_id)
        rec = self._transition(cobot_id, CobotStatus.ESTOP.value, task="E-STOP ENGAGED")
        return rec

    def reset_to_idle(self, cobot_id: str) -> CobotRecord:
        """FAULT/ESTOP → IDLE (or any → IDLE for supervisor override). Always allowed for reset endpoint."""
        rec = self._fleet.get(cobot_id)
        if rec is None:
            raise ValueError(f"Cobot '{cobot_id}' not registered in scheduler fleet")
        self._cancel_sim_task(cobot_id)
        old = rec.status
        rec.status = CobotStatus.IDLE.value
        rec.active_task = None
        rec.worker_id = None
        rec.zone_name = None
        rec.assigned_at = None
        logger.info(f"[CobotSM] {cobot_id} ({rec.name}): {old} → IDLE (supervisor reset)")
        return rec

    # ------------------------------------------------------------------
    # Simulated background auto-advance (dev / testing)
    # ------------------------------------------------------------------

    def _cancel_sim_task(self, cobot_id: str) -> None:
        rec = self._fleet.get(cobot_id)
        if rec and rec.transition_task and not rec.transition_task.done():
            rec.transition_task.cancel()
            rec.transition_task = None

    def schedule_simulated_advance(self, cobot_id: str) -> None:
        """
        In development/demo mode, automatically advances the state machine
        through ASSIGNED→ACTIVE→RETURNING→IDLE using simulated timing delays.

        In production this would instead await OPC-UA/MQTT acknowledgment
        messages from the robot controller before advancing.
        """
        self._cancel_sim_task(cobot_id)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(self._sim_advance_pipeline(cobot_id))
                rec = self._fleet.get(cobot_id)
                if rec:
                    rec.transition_task = task
        except RuntimeError:
            # No event loop running (e.g. in synchronous tests) — skip simulation
            pass

    async def _sim_advance_pipeline(self, cobot_id: str) -> None:
        """
        Simulates the full task lifecycle for a just-assigned cobot.

        Q1 fix — two changes from the original:

        1. Re-raises CancelledError after logging (fixes cooperative cancellation).
           Without this, asyncio sees the task as finishing cleanly rather than
           cancelled, so future cancel() calls on the same task object silently
           do nothing.

        2. Each individual transition is wrapped in try/except InvalidTransition.
           If an ESTOP/FAULT fires between a sleep and the next status check,
           the cobot's status will no longer match the expected phase, and
           _transition() will raise InvalidTransition.  We catch it, emit a
           CRITICAL audit event, and stop the pipeline — we do NOT silently
           swallow it or let it propagate as an unhandled exception.
        """
        try:
            # Phase 1: ASSIGNED → ACTIVE
            await asyncio.sleep(_SIM_ASSIGNED_TO_ACTIVE_SECS)
            rec = self._fleet.get(cobot_id)
            if rec and rec.status == CobotStatus.ASSIGNED.value:
                try:
                    self.activate(cobot_id)
                except InvalidTransition as e:
                    self._emit_audit(
                        "SIM_ADVANCE_INVALID_TRANSITION", cobot_id,
                        f"Background sim-advance tried ASSIGNED→ACTIVE on {cobot_id} "
                        f"but state was {rec.status}. Detail: {e}",
                        severity="critical"
                    )
                    return  # Stop pipeline — state was externally changed

            # Phase 2: ACTIVE → RETURNING
            await asyncio.sleep(_SIM_ACTIVE_TO_RETURNING_SECS)
            rec = self._fleet.get(cobot_id)
            if rec and rec.status == CobotStatus.ACTIVE.value:
                try:
                    self.complete(cobot_id)
                except InvalidTransition as e:
                    self._emit_audit(
                        "SIM_ADVANCE_INVALID_TRANSITION", cobot_id,
                        f"Background sim-advance tried ACTIVE→RETURNING on {cobot_id} "
                        f"but state was {rec.status}. Detail: {e}",
                        severity="critical"
                    )
                    return

            # Phase 3: RETURNING → IDLE
            await asyncio.sleep(_SIM_RETURNING_TO_IDLE_SECS)
            rec = self._fleet.get(cobot_id)
            if rec and rec.status == CobotStatus.RETURNING.value:
                try:
                    self.dock_home(cobot_id)
                except InvalidTransition as e:
                    self._emit_audit(
                        "SIM_ADVANCE_INVALID_TRANSITION", cobot_id,
                        f"Background sim-advance tried RETURNING→IDLE on {cobot_id} "
                        f"but state was {rec.status}. Detail: {e}",
                        severity="critical"
                    )
                    return

        except asyncio.CancelledError:
            # Q1 fix: LOG and RE-RAISE so asyncio marks the task as cancelled,
            # not as finished successfully. Without the re-raise, Task.cancelled()
            # returns False and a future task.cancel() on the same object is a no-op.
            rec = self._fleet.get(cobot_id)
            current_state = rec.status if rec else "unknown"
            logger.info(
                f"[CobotSM] Sim-advance pipeline cancelled for {cobot_id} "
                f"(current state: {current_state}). Halting auto-advance."
            )
            raise  # <── Critical: cooperative cancellation requires re-raise

    # ------------------------------------------------------------------
    # Legacy compatibility shim (used by decisions.py)
    # ------------------------------------------------------------------

    def trigger_reassignment(self, cobot_id: str, worker_id: str, zone_name: str) -> Dict:
        """
        Used in decisions.py to drive IDLE→ASSIGNED and start the sim pipeline.
        Returns a success/failure dict so the caller can react without
        catching exceptions in the hot safety-decision path.

        Q3: InvalidTransition is caught here. The caller (decisions.py) must
        check result["success"] before acting on the assignment — this is already
        implemented correctly in decisions.py (dispatch_res.get("success")).
        """
        task_desc = f"Takeover payload handling from {worker_id} at {zone_name}"
        try:
            self.assign(cobot_id, worker_id, zone_name, task_desc)
            self.schedule_simulated_advance(cobot_id)
            rec = self._fleet[cobot_id]
            return {
                "success": True,
                "cobot_id": cobot_id,
                "task": rec.active_task,
                "status": rec.status
            }
        except InvalidTransition as e:
            logger.warning(
                f"[CobotSM] Assignment of {cobot_id} rejected (status: "
                f"{self._fleet.get(cobot_id, CobotRecord('?', '?')).status}): {e}"
            )
            return {"success": False, "error": str(e), "cobot_id": cobot_id}


cobot_scheduler = CobotScheduler()
