"""
schemas/safety.py
-----------------
Pydantic v2 schemas for API validation, serialization, and OpenAPI documentation.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, EmailStr, Field
from core.security import UserRole
from models.zone import RiskTier
from models.cobot import CobotStatus
from models.decision import SafetyAction
from models.alert import AlertSeverity


# --- Auth & User Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    expires_in_minutes: int


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: UserRole = UserRole.SUPERVISOR
    site_id: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    site_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Camera Schemas ---
class CameraCreate(BaseModel):
    name: str = Field(..., example="Assembly Cell 4 Overhead")
    stream_url: str = Field(..., example="rtsp://10.83.16.200:554/live")
    protocol: str = "RTSP"
    location_description: Optional[str] = "Zone C - Robotic Workstation"
    fps_target: int = 5


class CameraResponse(BaseModel):
    id: str
    name: str
    stream_url: str
    protocol: str
    location_description: Optional[str]
    fps_target: int
    status: str
    site_id: str
    created_at: datetime

    class Config:
        from_attributes = True


# --- Safety Zone Schemas ---
class ZoneCreate(BaseModel):
    camera_id: str
    name: str = Field(..., example="UR10e High Velocity Envelope")
    risk_tier: RiskTier = RiskTier.HIGH
    polygon_coordinates: List[List[float]] = Field(
        ...,
        example=[[0.65, 0.0], [1.0, 0.0], [1.0, 1.0], [0.65, 1.0]],
        description="List of [x, y] normalized coordinates (0.0 to 1.0) defining closed polygon"
    )
    color_hex: str = "#E85C4A"
    reassignment_eligible: bool = True
    deactivate_previous: bool = False


class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    risk_tier: Optional[RiskTier] = None
    polygon_coordinates: Optional[List[List[float]]] = None
    color_hex: Optional[str] = None
    reassignment_eligible: Optional[bool] = None
    change_reason: str = Field(..., example="Calibrated robot arm reach extension")


class ZoneResponse(BaseModel):
    id: str
    camera_id: str
    site_id: str
    name: str
    risk_tier: str
    polygon_coordinates: List[List[float]]
    color_hex: str
    reassignment_eligible: bool
    version: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# --- Worker Schemas ---
class WorkerCreate(BaseModel):
    display_alias: str = Field(..., example="Worker-Station-4")
    badge_reference: Optional[str] = None
    shift_code: str = "SHIFT-MORNING"


class WorkerResponse(BaseModel):
    id: str
    anonymized_token: str
    display_alias: str
    shift_code: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# --- Cobot Schemas ---
class CobotCreate(BaseModel):
    name: str = Field(..., example="UR10e-Cell-4")
    model: str = "Universal Robots UR10e"
    ip_address: Optional[str] = "192.168.1.100"
    payload_capacity_kg: float = 12.5


class CobotResponse(BaseModel):
    id: str
    name: str
    model: str
    status: str  # IDLE | ASSIGNED | ACTIVE | RETURNING | FAULT | ESTOP
    ip_address: Optional[str]
    payload_capacity_kg: float
    current_task_description: Optional[str] = None

    class Config:
        from_attributes = True


# --- Frame Analysis & Telemetry ---
class FrameAnalysisRequest(BaseModel):
    camera_id: Optional[str] = None
    worker_alias: Optional[str] = "Operator-01"
    image_base64: str = Field(..., description="Base64 encoded JPEG/PNG frame")


class TrackedWorkerState(BaseModel):
    worker_id: str
    center_norm: List[float]
    fatigue_score: float
    slump_angle: float
    stillness_seconds: float
    in_zone: bool
    zone_name: Optional[str] = None
    zone_risk: Optional[str] = None


class DecisionPayload(BaseModel):
    action: SafetyAction
    severity: str
    rule_fired: str
    fatigue_score: float
    in_zone: bool
    zone_label: Optional[str] = None
    cobot_reassigned: bool = False
    cobot_name: Optional[str] = None
    latency_ms: float
    decision_log_id: Optional[str] = None


class FrameAnalysisResponse(BaseModel):
    person_detected: bool
    tracked_workers: List[TrackedWorkerState] = []
    active_decision: Optional[DecisionPayload] = None
    zones: List[ZoneResponse] = []
    error: Optional[str] = None


# --- Explainability & Reports ---
class ExplainRequest(BaseModel):
    decision_log_id: Optional[str] = None
    use_llm: bool = True


class ExplainResponse(BaseModel):
    explanation: str
    source: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    osha_ready: bool = True


class IncidentReportExport(BaseModel):
    report_id: str
    timestamp: datetime
    facility: str
    camera: str
    worker_pseudonym: str
    action_enforced: str
    fatigue_metrics: Dict[str, Any]
    safety_zone_breach: Optional[str]
    plain_language_explanation: str
    compliance_disclaimer: str


# --- Alerts & Analytics ---
class AlertResponse(BaseModel):
    id: str
    title: str
    message: str
    severity: str
    acknowledged: bool
    escalation_level: int
    created_at: datetime

    class Config:
        from_attributes = True


class AlertAcknowledgeRequest(BaseModel):
    notes: Optional[str] = "Supervisor investigated and resolved safety hazard."


class SafetyMetricsSummary(BaseModel):
    active_cameras: int
    tracked_workers: int
    active_zones: int
    avg_fatigue_score: float
    total_decisions_24h: int
    cobot_reassignments_24h: int
    unacknowledged_alerts: int
