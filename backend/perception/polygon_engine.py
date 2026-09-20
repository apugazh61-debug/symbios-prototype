"""
perception/polygon_engine.py
----------------------------
High-performance geometric collision detection using Shapely.
Supports arbitrary N-sided polygons, point-in-polygon tests, and proximity safety buffers.
"""

from typing import List, Tuple, Optional, Dict, Any
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid


class SafetyPolygon:
    """Represents a validated 2D safety zone on a normalized [0, 1] camera plane."""
    def __init__(self, zone_id: str, label: str, risk_tier: str, coordinates: List[List[float]], reassignment_eligible: bool = True):
        self.zone_id = zone_id
        self.label = label
        self.risk_tier = risk_tier.lower()
        self.reassignment_eligible = reassignment_eligible
        self.raw_coordinates = coordinates
        
        # Build Shapely polygon (ensuring minimum 3 vertices)
        if len(coordinates) < 3:
            raise ValueError(f"Zone '{label}' must have at least 3 vertices. Got {len(coordinates)}")
        
        poly = Polygon(coordinates)
        if not poly.is_valid:
            poly = make_valid(poly)
        self.polygon: Polygon = poly

    def contains_point(self, norm_x: float, norm_y: float) -> bool:
        """Determines if normalized point (x, y) is inside the polygon boundary."""
        pt = Point(norm_x, norm_y)
        return self.polygon.contains(pt) or self.polygon.touches(pt)

    def distance_to_point(self, norm_x: float, norm_y: float) -> float:
        """Returns Euclidean distance from point to closest edge of polygon."""
        pt = Point(norm_x, norm_y)
        return float(self.polygon.distance(pt))


class PolygonCollisionEngine:
    """Manages active zones for a camera feed and computes worker-zone intersections."""
    def __init__(self):
        self._zones: Dict[str, SafetyPolygon] = {}

    def load_zones(self, zone_definitions: List[Dict[str, Any]]):
        """Replaces active zones with new definitions."""
        self._zones.clear()
        for z in zone_definitions:
            try:
                poly = SafetyPolygon(
                    zone_id=str(z.get("id", "")),
                    label=z.get("name") or z.get("label", "Safety Zone"),
                    risk_tier=z.get("risk_tier", "high"),
                    coordinates=z.get("polygon_coordinates") or z.get("coordinates", []),
                    reassignment_eligible=z.get("reassignment_eligible", True)
                )
                self._zones[poly.zone_id] = poly
            except Exception as e:
                # Log error and continue with remaining valid zones
                pass

    def check_point(self, norm_x: float, norm_y: float) -> Optional[SafetyPolygon]:
        """
        Checks if a point intersects any zone.
        Returns the highest risk intersecting zone, or None if safe.
        """
        severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        intersected: List[SafetyPolygon] = []

        for zone in self._zones.values():
            if zone.contains_point(norm_x, norm_y):
                intersected.append(zone)

        if not intersected:
            return None

        # Return highest severity zone
        intersected.sort(key=lambda z: severity_order.get(z.risk_tier, 0), reverse=True)
        return intersected[0]

    def get_zone_proximity(self, norm_x: float, norm_y: float, buffer_threshold: float = 0.08) -> Optional[Tuple[SafetyPolygon, float]]:
        """
        Detects if worker is dangerously close (within buffer) to a critical/high zone before breaching.
        """
        closest_zone = None
        min_dist = 999.0

        for zone in self._zones.values():
            dist = zone.distance_to_point(norm_x, norm_y)
            if dist < min_dist:
                min_dist = dist
                closest_zone = zone

        if closest_zone and min_dist <= buffer_threshold:
            return closest_zone, min_dist
        return None
