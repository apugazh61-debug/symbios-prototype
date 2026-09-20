"""
api/v1/ws.py
------------
WebSocket endpoints for streaming live safety alerts and video telemetry to dashboards.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from alerting.dispatcher import alert_dispatcher
from core.logging import logger

router = APIRouter(tags=["WebSocket Realtime Feed"])


@router.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """Duplex WebSocket feed for supervisory dashboards."""
    await alert_dispatcher.register_websocket(websocket)
    try:
        while True:
            # Receive client ping or keepalive
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        alert_dispatcher.unregister_websocket(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        alert_dispatcher.unregister_websocket(websocket)
