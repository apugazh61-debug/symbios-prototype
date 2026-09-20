"""
engine/interface.py
-------------------
Abstract State->Action interface for industrial safety decision making.
Decouples deterministic rule-based algorithms (v1) from Reinforcement Learning policies (v2).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from models.decision import SafetyAction


@dataclass
class WorkerStateContext:
    worker_id: str
    fatigue_score: float
    slump_angle: float
    stillness_seconds: float
    in_zone: bool
    zone_label: Optional[str] = None
    zone_risk_tier: Optional[str] = None
    reassignment_eligible: bool = False
    center_norm: List[float] = None


@dataclass
class CobotContext:
    available_cobot_id: Optional[str] = None
    cobot_name: Optional[str] = None
    cobot_status: str = "IDLE"
    active_queue_depth: int = 0


@dataclass
class SafetyDecisionResult:
    action: SafetyAction
    severity: str  # "info", "warning", "critical"
    rule_fired: str
    reassign_cobot: bool = False
    assigned_cobot_id: Optional[str] = None
    assigned_cobot_name: Optional[str] = None
    reason: str = ""
    engine_version: str = "v1-rules"
    metadata: Dict[str, Any] = None


class DecisionEngineBase(ABC):
    """Abstract contract that any safety decision model (heuristic, rule, or RL) must implement."""
    @abstractmethod
    def evaluate(self, worker: WorkerStateContext, cobot: CobotContext) -> SafetyDecisionResult:
        pass
