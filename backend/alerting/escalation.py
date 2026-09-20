"""
alerting/escalation.py
----------------------
Escalation supervisor daemon.
Scans for unacknowledged critical safety alerts and escalates to secondary channels.
"""

from datetime import datetime, timezone, timedelta
import asyncio
from core.logging import logger
from core.config import settings
from core.database import SessionLocal
from models.alert import SafetyAlert, AlertEscalation


class EscalationDaemon:
    """Monitors alerts in the database and triggers escalations."""
    def __init__(self, check_interval_seconds: int = 30):
        self.check_interval = check_interval_seconds
        self.is_running = False

    async def run_loop(self):
        self.is_running = True
        logger.info("Safety alert escalation daemon started.")
        while self.is_running:
            try:
                self.check_unacknowledged_alerts()
            except Exception as e:
                logger.error(f"Error in escalation daemon check: {e}")
            await asyncio.sleep(self.check_interval)

    def check_unacknowledged_alerts(self):
        db = SessionLocal()
        try:
            threshold_time = datetime.now(timezone.utc) - timedelta(minutes=settings.ALERT_ESCALATION_MINUTES)
            unacked = db.query(SafetyAlert).filter(
                SafetyAlert.acknowledged == False,
                SafetyAlert.severity.in_(["critical", "warning"]),
                SafetyAlert.created_at <= threshold_time,
                SafetyAlert.escalation_level == 0
            ).all()

            for alert in unacked:
                logger.warning(
                    f"ESCALATION: Safety Alert {alert.id} ('{alert.title}') unacknowledged for > "
                    f"{settings.ALERT_ESCALATION_MINUTES} mins. Escalating to EHS Manager."
                )
                alert.escalation_level += 1
                escalation = AlertEscalation(
                    alert_id=alert.id,
                    escalated_to_channel="SMS_PAGER",
                    recipient_target="EHS_DUTY_SUPERVISOR",
                    status="DISPATCHED"
                )
                db.add(escalation)

            db.commit()
        finally:
            db.close()

    def stop(self):
        self.is_running = False


escalation_daemon = EscalationDaemon()
