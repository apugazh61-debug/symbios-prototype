"""
api/v1/cameras.py
-----------------
Camera sensor registration and stream configuration endpoints.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import RoleChecker, UserRole, TokenPayload
from models.camera import Camera
from schemas.safety import CameraCreate, CameraResponse

router = APIRouter(prefix="/cameras", tags=["Camera Sensors"])


@router.get("", response_model=List[CameraResponse])
def list_cameras(db: Session = Depends(get_db)):
    """List all registered factory camera streams."""
    return db.query(Camera).all()


@router.post("", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def register_camera(
    camera_in: CameraCreate,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """Register a new RTSP/IP camera feed mapped to the factory floor."""
    site_id = token.site_id or "default-site-01"
    camera = Camera(
        site_id=site_id,
        name=camera_in.name,
        stream_url=camera_in.stream_url,
        protocol=camera_in.protocol,
        location_description=camera_in.location_description,
        fps_target=camera_in.fps_target,
        status="ONLINE"
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return camera


@router.get("/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: str, db: Session = Depends(get_db)):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera sensor not found")
    return camera
