"""
zones.py
--------
Defines danger/safety zones on the camera frame and checks whether a
worker's position falls inside one.

UPGRADED: Uses Shapely polygon collision detection with arbitrary N-sided
polygons from perception.polygon_engine.
"""

from typing import List, Optional, Tuple, Any
from perception.polygon_engine import SafetyPolygon, PolygonCollisionEngine

# Global collision engine instance for backwards compatibility
_default_engine = PolygonCollisionEngine()


def check_zones(center_norm: Tuple[float, float], zones: Optional[List[dict]] = None) -> Optional[SafetyPolygon]:
    """
    Checks if a normalized point (x, y) falls inside any defined polygon zone
    using exact Shapely point-in-polygon ray-casting.
    """
    if zones:
        engine = PolygonCollisionEngine()
        engine.load_zones(zones)
        return engine.check_point(center_norm[0], center_norm[1])
    return _default_engine.check_point(center_norm[0], center_norm[1])

