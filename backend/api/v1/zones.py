"""
api/v1/zones.py
---------------
Admin-managed Polygon Safety Zone configuration, geometric validation, and version audit history.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from shapely.geometry import Polygon
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import RoleChecker, UserRole, TokenPayload, get_current_user_token
from models.zone import SafetyZone, SafetyZoneHistory
from models.audit import AuditLog
from schemas.safety import ZoneCreate, ZoneUpdate, ZoneResponse

router = APIRouter(prefix="/zones", tags=["Safety Zone Management"])


def validate_polygon_coordinates(coords: List[List[float]]) -> List[List[float]]:
    """
    Validates polygon coordinates with Shapely:
    - Rejects fewer than 3 vertices with 400.
    - Rejects coordinates not normalized to [0.0, 1.0] with 400.
    - Rejects self-intersecting polygons (.is_valid is False) with 400.
    - Rejects zero-area/degenerate polygons with 400.
    """
    if not coords or len(coords) < 3:
        raise HTTPException(
            status_code=400,
            detail="A polygon must contain at least 3 vertices."
        )

    for pt in coords:
        if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
            raise HTTPException(
                status_code=400,
                detail=f"Each polygon vertex must be a 2D coordinate [x, y]. Got: {pt}"
            )
        try:
            x, y = float(pt[0]), float(pt[1])
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Polygon vertex coordinates must be numeric floats. Got: {pt}"
            )
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise HTTPException(
                status_code=400,
                detail=f"Polygon vertex coordinates must be normalized between 0.0 and 1.0. Got: [{x}, {y}]"
            )

    try:
        poly = Polygon(coords)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid polygon geometry: {str(e)}"
        )

    if not poly.is_valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid polygon: this shape crosses itself (self-intersecting boundary). Please redraw without crossing edges."
        )

    if poly.area <= 1e-6:
        raise HTTPException(
            status_code=400,
            detail="Invalid polygon: geometry has zero or negligible area (degenerate line/point)."
        )

    return coords


@router.get("", response_model=List[ZoneResponse])
def list_safety_zones(
    camera_id: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """Retrieve all safety zones, optionally filtered by camera."""
    query = db.query(SafetyZone)
    if camera_id:
        query = query.filter(SafetyZone.camera_id == camera_id)
    if active_only:
        query = query.filter(SafetyZone.is_active == True)
    
    zones = query.all()
    results = []
    for z in zones:
        coords = json.loads(z.polygon_geojson) if z.polygon_geojson else []
        results.append(
            ZoneResponse(
                id=z.id,
                camera_id=z.camera_id,
                site_id=z.site_id,
                name=z.name,
                risk_tier=z.risk_tier,
                polygon_coordinates=coords,
                color_hex=z.color_hex,
                reassignment_eligible=z.reassignment_eligible,
                version=z.version,
                is_active=z.is_active,
                created_at=z.created_at
            )
        )
    return results


@router.post("", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
def create_safety_zone(
    zone_in: ZoneCreate,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """
    Create a new polygon safety zone with validated coordinates.
    If deactivate_previous is True, any existing active zones for the specified
    camera are deactivated within the SAME atomic database transaction, guaranteeing
    zero-gap transition continuity for perception and safety checks.
    """
    validated_coords = validate_polygon_coordinates(zone_in.polygon_coordinates)

    # Atomic zone replacement: deactivate old zones for the camera in the SAME transaction
    if zone_in.deactivate_previous and zone_in.camera_id:
        db.query(SafetyZone).filter(
            SafetyZone.camera_id == zone_in.camera_id,
            SafetyZone.is_active == True
        ).update({"is_active": False})

    site_id = token.site_id or "default-site-01"
    new_zone = SafetyZone(
        site_id=site_id,
        camera_id=zone_in.camera_id,
        name=zone_in.name,
        risk_tier=zone_in.risk_tier.value,
        polygon_geojson=json.dumps(validated_coords),
        color_hex=zone_in.color_hex,
        reassignment_eligible=zone_in.reassignment_eligible,
        version=1,
        is_active=True
    )
    db.add(new_zone)
    db.flush()

    # Initial history entry
    hist = SafetyZoneHistory(
        zone_id=new_zone.id,
        modified_by_user_id=token.sub,
        version=1,
        previous_polygon=None,
        new_polygon=new_zone.polygon_geojson,
        change_reason="Initial zone definition creation"
    )
    db.add(hist)

    who = token.email
    user_id = token.sub

    audit = AuditLog(
        user_id=user_id,
        user_email=who,
        action="CREATE_SAFETY_ZONE",
        entity_type="SafetyZone",
        entity_id=new_zone.id,
        severity="info",
        message=f"Supervisor '{who}' created safety zone '{new_zone.name}' with {len(validated_coords)} vertices.",
        changes_json=json.dumps({
            "zone_name": new_zone.name,
            "risk_tier": new_zone.risk_tier,
            "polygon_coordinates": validated_coords,
            "created_by": who
        })
    )
    db.add(audit)
    db.commit()
    db.refresh(new_zone)

    return ZoneResponse(
        id=new_zone.id,
        camera_id=new_zone.camera_id,
        site_id=new_zone.site_id,
        name=new_zone.name,
        risk_tier=new_zone.risk_tier,
        polygon_coordinates=validated_coords,
        color_hex=new_zone.color_hex,
        reassignment_eligible=new_zone.reassignment_eligible,
        version=new_zone.version,
        is_active=new_zone.is_active,
        created_at=new_zone.created_at
    )


@router.put("/{zone_id}", response_model=ZoneResponse)
def update_safety_zone(
    zone_id: str,
    zone_in: ZoneUpdate,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """
    Updates safety zone geometry with an immutable audit trail entry.
    Updates the existing row in place in a single atomic database transaction,
    guaranteeing that the zone remains active with zero downtime/gap.
    """
    zone = db.query(SafetyZone).filter(SafetyZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Safety zone not found")

    old_polygon = zone.polygon_geojson

    if zone_in.name:
        zone.name = zone_in.name
    if zone_in.risk_tier:
        zone.risk_tier = zone_in.risk_tier.value
    if zone_in.color_hex:
        zone.color_hex = zone_in.color_hex
    if zone_in.reassignment_eligible is not None:
        zone.reassignment_eligible = zone_in.reassignment_eligible

    if zone_in.polygon_coordinates is not None:
        validated_coords = validate_polygon_coordinates(zone_in.polygon_coordinates)
        zone.polygon_geojson = json.dumps(validated_coords)

    zone.version += 1

    # Record history
    hist = SafetyZoneHistory(
        zone_id=zone.id,
        modified_by_user_id=token.sub,
        version=zone.version,
        previous_polygon=old_polygon,
        new_polygon=zone.polygon_geojson,
        change_reason=zone_in.change_reason
    )
    db.add(hist)

    who = token.email
    user_id = token.sub

    audit = AuditLog(
        user_id=user_id,
        user_email=who,
        action="UPDATE_SAFETY_ZONE",
        entity_type="SafetyZone",
        entity_id=zone.id,
        severity="info",
        message=f"Supervisor '{who}' updated safety zone '{zone.name}' (v{zone.version}): {zone_in.change_reason or 'Geometry updated'}.",
        changes_json=json.dumps({
            "zone_name": zone.name,
            "version": zone.version,
            "polygon_coordinates": json.loads(zone.polygon_geojson),
            "reason": zone_in.change_reason,
            "modified_by": who
        })
    )
    db.add(audit)
    db.commit()
    db.refresh(zone)

    return ZoneResponse(
        id=zone.id,
        camera_id=zone.camera_id,
        site_id=zone.site_id,
        name=zone.name,
        risk_tier=zone.risk_tier,
        polygon_coordinates=json.loads(zone.polygon_geojson),
        color_hex=zone.color_hex,
        reassignment_eligible=zone.reassignment_eligible,
        version=zone.version,
        is_active=zone.is_active,
        created_at=zone.created_at
    )


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_safety_zone(
    zone_id: str,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN])),
    db: Session = Depends(get_db)
):
    """Soft deletes/deactivates safety zone with immutable audit trail."""
    zone = db.query(SafetyZone).filter(SafetyZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Safety zone not found")
    zone.is_active = False

    audit = AuditLog(
        user_id=token.sub,
        user_email=token.email,
        action="DEACTIVATE_SAFETY_ZONE",
        entity_type="SafetyZone",
        entity_id=zone.id,
        severity="warning",
        message=f"Admin '{token.email}' deactivated safety zone '{zone.name}' (id: {zone.id}).",
        changes_json=json.dumps({
            "zone_id": zone.id,
            "zone_name": zone.name,
            "previous_active": True,
            "is_active": False,
            "deactivated_by": token.email
        })
    )
    db.add(audit)
    db.commit()
