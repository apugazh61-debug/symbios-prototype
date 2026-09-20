"""
core/config.py
--------------
Centralized, validated configuration for the SYMBIOS enterprise platform
using Pydantic Settings. Supports .env overrides and environment variables.
"""

from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Project Info
    PROJECT_NAME: str = "SYMBIOS Industrial Safety Platform"
    VERSION: str = "2.0.0-enterprise"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"  # development, staging, production
    DEBUG: bool = True

    # Security & Auth
    SECRET_KEY: str = Field(
        default="symbios-enterprise-safety-secret-key-change-in-production-32bytes-min!",
        description="HMAC secret key for JWT signing"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12-hour shift duration

    # Bootstrap Credentials (Never hardcode secrets in code or defaults)
    BOOTSTRAP_ADMIN_PASSWORD: Optional[str] = None
    BOOTSTRAP_SUPERVISOR_PASSWORD: Optional[str] = None

    # Database
    # Defaults to SQLite with absolute path for zero-config testing; set to postgresql+psycopg2://... in production
    DATABASE_URL: str = "sqlite:///" + str(__import__("pathlib").Path(__file__).resolve().parent.parent / "symbios_enterprise.db").replace("\\", "/")
    DB_ECHO: bool = False

    # Redis Message Queue & PubSub
    REDIS_URL: Optional[str] = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "*"
    ]

    # Fatigue Scoring Tunable Thresholds
    FATIGUE_WINDOW_SECONDS: int = 30
    FATIGUE_SAMPLE_INTERVAL_MS: int = 800
    FATIGUE_SLUMP_THRESHOLD_DEGREES: float = 18.0  # Angle deviation for slouch
    FATIGUE_STILLNESS_THRESHOLD_SECONDS: float = 8.0
    FATIGUE_SCORE_MONITOR_THRESHOLD: float = 35.0
    FATIGUE_SCORE_CRITICAL_THRESHOLD: float = 65.0

    # Safety Zone Escalation
    ALERT_ESCALATION_MINUTES: int = 3

    # LLM Explainability
    ANTHROPIC_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    EXPLAIN_MODEL_FALLBACK: str = "deterministic_template"

    # Compliance & Data Retention
    RETAIN_RAW_VIDEO: bool = False  # Strictly False for GDPR / privacy compliance
    SKELETAL_METRICS_RETENTION_DAYS: int = 90
    AUDIT_LOG_RETENTION_DAYS: int = 365 * 3  # 3-year OSHA safety compliance retention

    # Webhooks & Integrations
    SLACK_WEBHOOK_URL: Optional[str] = None
    TEAMS_WEBHOOK_URL: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
