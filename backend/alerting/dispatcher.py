"""
alerting/dispatcher.py
----------------------
Multi-channel industrial safety alerting dispatcher.
Broadcasts to connected WebSocket dashboards, Slack/Teams webhooks, and SMS.
"""

from typing import Dict, Any, List
import asyncio
from fastapi import WebSocket
from core.logging import logger
from core.config import settings


class NotificationDispatcher:
    """Manages live WebSocket dashboard connections and multi-channel alerting."""
    def __init__(self):
        self.active_websockets: List[WebSocket] = []

    async def register_websocket(self, websocket: WebSocket):
        await websocket.accept()
        self.active_websockets.append(websocket)
        logger.info(f"Dashboard client connected. Active connections: {len(self.active_websockets)}")

    def unregister_websocket(self, websocket: WebSocket):
        if websocket in self.active_websockets:
            self.active_websockets.remove(websocket)
            logger.info(f"Dashboard client disconnected. Active connections: {len(self.active_websockets)}")

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        """Dispatches event to all active supervisory dashboards."""
        payload = {"type": event_type, "payload": data}
        dead_sockets = []
        for ws in self.active_websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead_sockets.append(ws)

        for ws in dead_sockets:
            self.unregister_websocket(ws)

    async def dispatch_critical_alert(self, alert_data: Dict[str, Any]):
        """Dispatches high/critical alerts to external enterprise channels."""
        # 1. Live dashboard broadcast
        await self.broadcast_event("SAFETY_ALERT", alert_data)

        # 2. Slack / Teams Webhook if configured
        if settings.SLACK_WEBHOOK_URL:
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post(
                        settings.SLACK_WEBHOOK_URL,
                        json={"text": f"🚨 [SYMBIOS SAFETY ALERT] {alert_data.get('title')}: {alert_data.get('message')}"},
                        timeout=3.0
                    )
            except Exception as e:
                logger.error(f"Failed to post to Slack webhook: {e}")


alert_dispatcher = NotificationDispatcher()
