"""
tests/test_zone_geometry.py
---------------------------
Unit tests for Shapely-based polygon containment, edge proximity, and multi-tier risk ordering.
"""

import pytest
from perception.polygon_engine import SafetyPolygon, PolygonCollisionEngine


def test_polygon_containment_basic():
    # Define rectangular zone on normalized [0, 1] frame
    coords = [[0.6, 0.0], [1.0, 0.0], [1.0, 1.0], [0.6, 1.0]]
    poly = SafetyPolygon(
        zone_id="zone-01",
        label="Robotic Envelope",
        risk_tier="high",
        coordinates=coords
    )

    # Inside points
    assert poly.contains_point(0.8, 0.5) is True
    assert poly.contains_point(0.99, 0.99) is True

    # Outside points
    assert poly.contains_point(0.2, 0.5) is False
    assert poly.contains_point(0.1, 0.1) is False
    assert poly.contains_point(0.59, 0.5) is False


def test_concave_irregular_polygon():
    # L-shaped polygon
    coords = [
        [0.0, 0.0],
        [0.8, 0.0],
        [0.8, 0.4],
        [0.4, 0.4],
        [0.4, 0.8],
        [0.0, 0.8]
    ]
    poly = SafetyPolygon(
        zone_id="zone-concave",
        label="Conveyor Line L-Bend",
        risk_tier="critical",
        coordinates=coords
    )

    # Point inside the lower branch
    assert poly.contains_point(0.2, 0.6) is True
    # Point inside the upper branch
    assert poly.contains_point(0.6, 0.2) is True
    # Point in the cutout notch (should be False)
    assert poly.contains_point(0.6, 0.6) is False


def test_collision_engine_multi_tier_risk_priority():
    engine = PolygonCollisionEngine()
    # Overlapping zones with different risk tiers
    engine.load_zones([
        {
            "id": "zone-medium",
            "name": "Medium Caution Area",
            "risk_tier": "medium",
            "coordinates": [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]],
            "reassignment_eligible": True
        },
        {
            "id": "zone-critical",
            "name": "E-Stop Pinch Point",
            "risk_tier": "critical",
            "coordinates": [[0.7, 0.2], [0.9, 0.2], [0.9, 0.8], [0.7, 0.8]],
            "reassignment_eligible": True
        }
    ])

    # Point in medium only
    res_med = engine.check_point(0.55, 0.5)
    assert res_med is not None
    assert res_med.risk_tier == "medium"

    # Point in both medium and critical -> engine must prioritize critical
    res_crit = engine.check_point(0.8, 0.5)
    assert res_crit is not None
    assert res_crit.risk_tier == "critical"
    assert res_crit.label == "E-Stop Pinch Point"


def test_proximity_buffer_detection():
    engine = PolygonCollisionEngine()
    engine.load_zones([
        {
            "id": "zone-critical",
            "name": "Pinch Point",
            "risk_tier": "critical",
            "coordinates": [[0.7, 0.0], [1.0, 0.0], [1.0, 1.0], [0.7, 1.0]]
        }
    ])

    # Worker is at 0.65 (distance is 0.05 from 0.70 boundary)
    prox = engine.get_zone_proximity(0.65, 0.5, buffer_threshold=0.08)
    assert prox is not None
    zone, dist = prox
    assert zone.label == "Pinch Point"
    assert round(dist, 2) == 0.05


# ==============================================================================
# Step 3 Test Suite: Validation, Atomic Transactions & Concave Geometry
# ==============================================================================
import json
import uuid
from fastapi.testclient import TestClient
from main import app
from core.security import get_bootstrap_supervisor_password
from core.database import SessionLocal
from models.zone import SafetyZone
from zones import check_zones


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def get_supervisor_auth_headers(client):
    """Authenticate as supervisor to test admin/supervisor zone configuration endpoints."""
    res = client.post(
        "/api/v1/auth/token",
        data={"username": "supervisor@symbios.ai", "password": get_bootstrap_supervisor_password()}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_polygon_validation_rejects_fewer_than_three_vertices(client):
    """Shapely validation must reject polygons with fewer than 3 vertices with HTTP 400."""
    headers = get_supervisor_auth_headers(client)

    # 1. Two vertices
    res = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-test-val",
            "name": "Line Hazard",
            "risk_tier": "high",
            "polygon_coordinates": [[0.1, 0.1], [0.5, 0.5]]
        }
    )
    assert res.status_code == 400
    assert "at least 3 vertices" in res.json()["detail"]

    # 2. Empty vertices
    res_empty = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-test-val",
            "name": "Empty Hazard",
            "risk_tier": "high",
            "polygon_coordinates": []
        }
    )
    assert res_empty.status_code == 400
    assert "at least 3 vertices" in res_empty.json()["detail"]


def test_polygon_validation_rejects_self_intersecting_shape(client):
    """
    Shapely validation must reject self-intersecting (bowtie/hourglass) polygons
    with HTTP 400 and clear error message 'this shape crosses itself'.
    """
    headers = get_supervisor_auth_headers(client)

    # Self-intersecting bowtie polygon
    bowtie_coords = [
        [0.1, 0.1],
        [0.8, 0.8],
        [0.1, 0.8],
        [0.8, 0.1]
    ]

    # Test on POST (creation)
    res_create = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-test-bowtie",
            "name": "Bowtie Trap",
            "risk_tier": "critical",
            "polygon_coordinates": bowtie_coords
        }
    )
    assert res_create.status_code == 400
    assert "crosses itself" in res_create.json()["detail"]

    # Test on PUT (update)
    # First create valid zone
    res_valid = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-test-bowtie",
            "name": "Valid Triangle",
            "risk_tier": "high",
            "polygon_coordinates": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]
        }
    )
    assert res_valid.status_code == 201
    zone_id = res_valid.json()["id"]

    # Attempt to update it to a self-intersecting polygon
    res_update = client.put(
        f"/api/v1/zones/{zone_id}",
        headers=headers,
        json={
            "polygon_coordinates": bowtie_coords,
            "change_reason": "Attempting invalid geometry"
        }
    )
    assert res_update.status_code == 400
    assert "crosses itself" in res_update.json()["detail"]


def test_polygon_validation_rejects_out_of_bounds_and_zero_area(client):
    """Rejects coordinates not in [0.0, 1.0] and zero-area collinear points with HTTP 400."""
    headers = get_supervisor_auth_headers(client)

    # Out of bounds (> 1.0)
    res_oob = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-test-oob",
            "name": "OOB Hazard",
            "risk_tier": "high",
            "polygon_coordinates": [[1.2, 0.5], [0.5, 0.5], [0.5, 0.0]]
        }
    )
    assert res_oob.status_code == 400
    assert "normalized between 0.0 and 1.0" in res_oob.json()["detail"]

    # Collinear points (zero area / degenerate line)
    res_collinear = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": "cam-test-zero",
            "name": "Zero Area Hazard",
            "risk_tier": "high",
            "polygon_coordinates": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]
        }
    )
    assert res_collinear.status_code == 400
    assert (
        "crosses itself" in res_collinear.json()["detail"]
        or "zero or negligible area" in res_collinear.json()["detail"]
    )


def test_atomic_zone_update_prevents_zero_active_gap(client):
    """
    Requirement 1 & 3:
    When updating an existing zone's polygon:
    1. In-place update (PUT /zones/{id}) modifies geometry in-place within a single transaction.
    2. Atomic replacement (POST /zones with deactivate_previous=True) deactivates old and creates new
       in the SAME atomic transaction.
    Assert that at NO point does a worker-position check or active zone query against that workcell
    return 'no active zone' or find 0 active zones.
    """
    headers = get_supervisor_auth_headers(client)
    cam_id = f"cam-atomic-{uuid.uuid4().hex[:6]}"
    
    # 1. Initial zone definition
    initial_coords = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    res_init = client.post(
        "/api/v1/zones",
        headers=headers,
        json={
            "camera_id": cam_id,
            "name": "Primary Hazard Cell",
            "risk_tier": "critical",
            "polygon_coordinates": initial_coords
        }
    )
    assert res_init.status_code == 201
    zone_id = res_init.json()["id"]

    db = SessionLocal()
    try:
        # Pre-update check: exactly 1 active zone exists
        active_zones = db.query(SafetyZone).filter(
            SafetyZone.camera_id == cam_id,
            SafetyZone.is_active == True
        ).all()
        assert len(active_zones) == 1
        assert active_zones[0].id == zone_id

        # Worker at (0.5, 0.5) is inside the active zone
        engine = PolygonCollisionEngine()
        engine.load_zones([{
            "id": active_zones[0].id,
            "name": active_zones[0].name,
            "risk_tier": active_zones[0].risk_tier,
            "polygon_coordinates": json.loads(active_zones[0].polygon_geojson)
        }])
        match = engine.check_point(0.5, 0.5)
        assert match is not None, "Worker should be detected inside active zone before update"

        # 2. In-place atomic update: change coordinates to slightly adjusted envelope
        updated_coords = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
        res_put = client.put(
            f"/api/v1/zones/{zone_id}",
            headers=headers,
            json={
                "name": "Primary Hazard Cell (Calibrated)",
                "polygon_coordinates": updated_coords,
                "change_reason": "Robot payload reach recalibration"
            }
        )
        assert res_put.status_code == 200
        assert res_put.json()["version"] == 2
        
        # In DB (expire identity map cache to read freshly committed data)
        db.expire_all()
        active_zones_after_put = db.query(SafetyZone).filter(
            SafetyZone.camera_id == cam_id,
            SafetyZone.is_active == True
        ).all()
        assert len(active_zones_after_put) == 1
        assert active_zones_after_put[0].version == 2
        assert active_zones_after_put[0].is_active is True

        # Assert worker check STILL matches the active zone (zero gap)
        engine.load_zones([{
            "id": active_zones_after_put[0].id,
            "name": active_zones_after_put[0].name,
            "risk_tier": active_zones_after_put[0].risk_tier,
            "polygon_coordinates": json.loads(active_zones_after_put[0].polygon_geojson)
        }])
        match_after_put = engine.check_point(0.5, 0.5)
        assert match_after_put is not None, "Worker check must NOT return 'no active zone' after in-place update"
        assert match_after_put.label == "Primary Hazard Cell (Calibrated)"

        # 3. Atomic replacement via POST with deactivate_previous=True
        replacement_coords = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
        res_replace = client.post(
            "/api/v1/zones",
            headers=headers,
            json={
                "camera_id": cam_id,
                "name": "Primary Hazard Cell (Replaced)",
                "risk_tier": "critical",
                "polygon_coordinates": replacement_coords,
                "deactivate_previous": True
            }
        )
        assert res_replace.status_code == 201
        new_zone_id = res_replace.json()["id"]

        # In DB, verify exactly 1 active zone exists for that workcell (old deactivated, new active)
        db.expire_all()
        active_zones_replaced = db.query(SafetyZone).filter(
            SafetyZone.camera_id == cam_id,
            SafetyZone.is_active == True
        ).all()
        assert len(active_zones_replaced) == 1
        assert active_zones_replaced[0].id == new_zone_id

        # Verify old zone was safely deactivated
        old_zone_row = db.query(SafetyZone).filter(SafetyZone.id == zone_id).first()
        assert old_zone_row.is_active is False

        # Assert worker at (0.5, 0.5) is continuously protected
        engine.load_zones([{
            "id": active_zones_replaced[0].id,
            "name": active_zones_replaced[0].name,
            "risk_tier": active_zones_replaced[0].risk_tier,
            "polygon_coordinates": json.loads(active_zones_replaced[0].polygon_geojson)
        }])
        match_replaced = engine.check_point(0.5, 0.5)
        assert match_replaced is not None, "Worker must NOT return 'no active zone' during atomic replacement"
        assert match_replaced.label == "Primary Hazard Cell (Replaced)"

    finally:
        db.close()


def test_decision_engine_concave_shape_containment_not_bounding_box():
    """
    Confirms Shapely-based polygon containment has completely replaced
    any rectangular bounding-box approximation.
    In an L-shaped polygon:
    - Worker in the cutout notch (0.6, 0.6) is OUTSIDE the polygon (would have been a false positive in a bbox).
    - Worker in the vertical branch (0.2, 0.6) is INSIDE the polygon.
    - Worker in the horizontal branch (0.6, 0.2) is INSIDE the polygon.
    """
    l_shape_coords = [
        [0.0, 0.0],
        [0.8, 0.0],
        [0.8, 0.4],
        [0.4, 0.4],
        [0.4, 0.8],
        [0.0, 0.8]
    ]

    zones_def = [{
        "id": "zone-l-bend",
        "name": "L-Bend Robotic Conveyor",
        "risk_tier": "critical",
        "coordinates": l_shape_coords,
        "reassignment_eligible": True
    }]

    # 1. Test via PolygonCollisionEngine
    engine = PolygonCollisionEngine()
    engine.load_zones(zones_def)

    # Worker standing in notch (0.6, 0.6) -> Bounding box [0..0.8, 0..0.8] would contain it,
    # but Shapely polygon MUST evaluate to None (safe)
    notch_res = engine.check_point(0.6, 0.6)
    assert notch_res is None, "Worker in concave cutout notch must NOT trigger zone breach"

    # Worker standing inside lower branch (0.2, 0.6)
    lower_res = engine.check_point(0.2, 0.6)
    assert lower_res is not None
    assert lower_res.label == "L-Bend Robotic Conveyor"
    assert lower_res.risk_tier == "critical"

    # Worker standing inside right branch (0.6, 0.2)
    right_res = engine.check_point(0.6, 0.2)
    assert right_res is not None
    assert right_res.label == "L-Bend Robotic Conveyor"

    # 2. Test via legacy zones.py module check_zones to guarantee backwards compatibility
    legacy_notch = check_zones((0.6, 0.6), zones_def)
    assert legacy_notch is None, "Legacy check_zones must correctly reject cutout notch"

    legacy_inside = check_zones((0.2, 0.6), zones_def)
    assert legacy_inside is not None, "Legacy check_zones must accurately identify inside point"
    assert legacy_inside.label == "L-Bend Robotic Conveyor"
