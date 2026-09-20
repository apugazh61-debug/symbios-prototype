"""
main.py
-------
SYMBIOS Industrial Safety & Workforce Orchestration Platform — Main API Gateway.
"""

from contextlib import asynccontextmanager
import asyncio
import json
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from core.config import settings
from core.database import Base, engine, SessionLocal
from core.security import (
    get_password_hash,
    UserRole,
    get_bootstrap_admin_password,
    get_bootstrap_supervisor_password
)
from core.logging import logger
from models.org import Company, Site
from models.user import User
from models.worker import Worker
from models.camera import Camera
from models.zone import SafetyZone, SafetyZoneHistory, RiskTier
from models.cobot import Cobot, CobotStatus
from api.v1 import api_v1_router
from alerting.escalation import escalation_daemon

# Legacy router endpoints for seamless backward compatibility
from api.v1.decisions import analyze_frame as v1_analyze_frame, get_latest_decision
from api.v1.zones import list_safety_zones as v1_list_zones
from api.v1.explain import explain_automated_decision as v1_explain
from schemas.safety import FrameAnalysisRequest, ExplainRequest


def seed_initial_enterprise_data():
    """Seeds baseline manufacturing plant, default admin, camera, and safety zones."""
    db = SessionLocal()
    try:
        admin_pw = get_bootstrap_admin_password()
        sup_pw = get_bootstrap_supervisor_password()

        # Check if already seeded
        company = db.query(Company).first()
        if not company:
            logger.info("Initializing baseline enterprise manufacturing seed data...")
            company = Company(
                id="comp-001",
                name="Apex Advanced Manufacturing Inc.",
                registration_code="APEX-US-001"
            )
            db.add(company)
            db.flush()

            site = Site(
                id="site-detroit-01",
                company_id=company.id,
                name="Detroit Assembly Plant 01",
                location="Detroit, MI, USA"
            )
            db.add(site)
            db.flush()

            # Seed users
            admin_user = User(
                id="user-admin-001",
                site_id=site.id,
                email="admin@symbios.ai",
                hashed_password=get_password_hash(admin_pw),
                full_name="Plant Safety Director",
                role=UserRole.ADMIN.value
            )
            supervisor_user = User(
                id="user-sup-001",
                site_id=site.id,
                email="supervisor@symbios.ai",
                hashed_password=get_password_hash(sup_pw),
                full_name="Shift Supervisor Sarah",
                role=UserRole.SUPERVISOR.value
            )
            db.add_all([admin_user, supervisor_user])

            # Seed default camera
            camera = Camera(
                id="cam-cell-04",
                site_id=site.id,
                name="Assembly Cell 4 - Overhead Stream",
                stream_url="rtsp://10.83.16.95:8554/live",
                protocol="RTSP",
                location_description="Cell 4 Palletizing & Collaborative Station",
                fps_target=5,
                status="ONLINE"
            )
            db.add(camera)
            db.flush()

            # Seed default safety zone with polygon
            default_poly = [[0.65, 0.0], [1.0, 0.0], [1.0, 1.0], [0.65, 1.0]]
            zone = SafetyZone(
                id="zone-cobot-envelope-01",
                site_id=site.id,
                camera_id=camera.id,
                name="Robotic Arm Envelope",
                risk_tier=RiskTier.HIGH.value,
                polygon_geojson=json.dumps(default_poly),
                color_hex="#E85C4A",
                reassignment_eligible=True,
                version=1,
                is_active=True
            )
            db.add(zone)
            db.flush()

            # Seed initial zone history
            hist = SafetyZoneHistory(
                zone_id=zone.id,
                modified_by_user_id=admin_user.id,
                version=1,
                previous_polygon=None,
                new_polygon=zone.polygon_geojson,
                change_reason="Factory commissioning baseline"
            )
            db.add(hist)

            # Seed physical Cobot station
            cobot = Cobot(
                id="cobot-cell-01",
                site_id=site.id,
                name="UR10e-Arm-North",
                model="Universal Robots UR10e",
                status=CobotStatus.IDLE.value,
                ip_address="10.83.16.120",
                payload_capacity_kg=12.5
            )
            db.add(cobot)

            # Seed worker
            worker = Worker(
                id="worker-anon-01",
                site_id=site.id,
                anonymized_token="OPERATOR_TOKEN_99A",
                badge_reference="ID-B772",
                display_alias="Operator-01",
                shift_code="SHIFT-MORNING",
                is_active=True
            )
            db.add(worker)

            db.commit()
            logger.info("Enterprise seed completed successfully.")
        else:
            # Sync existing bootstrap users with active startup credentials
            admin_u = db.query(User).filter(User.email == "admin@symbios.ai").first()
            if admin_u:
                admin_u.hashed_password = get_password_hash(admin_pw)
            sup_u = db.query(User).filter(User.email == "supervisor@symbios.ai").first()
            if sup_u:
                sup_u.hashed_password = get_password_hash(sup_pw)
            db.commit()
    except Exception as e:
        logger.error(f"Seeding error: {e}")
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown orchestration."""
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION} [{settings.ENVIRONMENT}]")
    
    # Fail-fast security check: verify bootstrap credentials at startup
    _ = get_bootstrap_admin_password()
    _ = get_bootstrap_supervisor_password()

    Base.metadata.create_all(bind=engine)

    # Ensure schema migrations for SQLite columns if upgraded from older schema
    try:
        with engine.connect() as conn:
            from sqlalchemy import text

            # audit_logs migrations
            res = conn.execute(text("PRAGMA table_info(audit_logs)")).fetchall()
            col_names = [r[1] for r in res]
            if "severity" not in col_names:
                conn.execute(text("ALTER TABLE audit_logs ADD COLUMN severity VARCHAR(20) DEFAULT 'info'"))
            if "message" not in col_names:
                conn.execute(text("ALTER TABLE audit_logs ADD COLUMN message VARCHAR(500)"))

            # cobots schema migrations — Step 2: state machine task description
            res = conn.execute(text("PRAGMA table_info(cobots)")).fetchall()
            cobot_cols = [r[1] for r in res]
            if "current_task_description" not in cobot_cols:
                conn.execute(text("ALTER TABLE cobots ADD COLUMN current_task_description TEXT"))
                logger.info("Schema migration: added cobots.current_task_description column")

            # cobots DATA migration — Step 2: normalise legacy enum values.
            # "BUSY" and "REASSIGNED" pre-date the real state machine and are no
            # longer valid status strings.  Any row left with those values will
            # cause a scheduler InvalidTransition on first access, so we convert
            # them to IDLE (safe resting state) at startup.
            legacy_rows = conn.execute(
                text("SELECT id, name, status FROM cobots WHERE status IN ('BUSY','REASSIGNED')")
            ).fetchall()
            if legacy_rows:
                conn.execute(
                    text("UPDATE cobots SET status='IDLE', current_task_description=NULL "
                         "WHERE status IN ('BUSY','REASSIGNED')")
                )
                for row in legacy_rows:
                    logger.warning(
                        f"Data migration: cobot '{row[0]}' ({row[1]}) had legacy status "
                        f"'{row[2]}' → normalised to IDLE"
                    )

            conn.commit()
    except Exception as e:
        logger.warning(f"Schema compatibility check notice: {e}")

    seed_initial_enterprise_data()

    # Launch background escalation supervisor
    escalation_task = asyncio.create_task(escalation_daemon.run_loop())
    yield
    escalation_daemon.stop()
    escalation_task.cancel()
    logger.info("SYMBIOS shutdown completed.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise Industrial Safety & Workforce Orchestration Platform with explainable decision automation.",
    lifespan=lifespan
)

# Prometheus Metrics exporter
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Cross-Origin Resource Sharing
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register v1 API Router
app.include_router(api_v1_router, prefix=settings.API_V1_STR)


# --- Root & Legacy Compatibility Endpoints ---
@app.get("/health", tags=["Health & Diagnostics"])
def health_check():
    """System liveness probe."""
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT
    }


# Backwards compatibility routes for live demo & single-page client
@app.get("/zones", tags=["Compatibility"])
def legacy_zones(db=Depends(SessionLocal)):
    """Legacy route matching original prototype signature."""
    from models.zone import SafetyZone
    zones = db.query(SafetyZone).filter(SafetyZone.is_active == True).all()
    out = []
    for z in zones:
        coords = json.loads(z.polygon_geojson) if z.polygon_geojson else []
        x_min = min(pt[0] for pt in coords) if coords else 0.65
        x_max = max(pt[0] for pt in coords) if coords else 1.0
        y_min = min(pt[1] for pt in coords) if coords else 0.0
        y_max = max(pt[1] for pt in coords) if coords else 1.0
        out.append({
            "id": z.id,
            "label": z.name,
            "x_min": x_min,
            "y_min": y_min,
            "x_max": x_max,
            "y_max": y_max,
            "risk_level": z.risk_tier,
            "polygon_coordinates": coords
        })
    db.close()
    return out


@app.post("/analyze_frame", tags=["Compatibility"])
async def legacy_analyze_frame(frame: FrameAnalysisRequest, db=Depends(SessionLocal)):
    """Legacy analyze route mapping to new pipeline."""
    res = await v1_analyze_frame(frame, db)
    db.close()
    if not res.person_detected:
        return {"person_detected": False}
    
    first_worker = res.tracked_workers[0] if res.tracked_workers else None
    return {
        "person_detected": True,
        "fatigue_score": first_worker.fatigue_score if first_worker else 0.0,
        "center_norm": first_worker.center_norm if first_worker else [0.5, 0.5],
        "decision": res.active_decision.dict() if res.active_decision else {}
    }


@app.post("/explain", tags=["Compatibility"])
def legacy_explain(req: ExplainRequest, db=Depends(SessionLocal)):
    """Legacy explain route."""
    res = v1_explain(req, db)
    db.close()
    return {"explanation": res.explanation, "source": res.source}
