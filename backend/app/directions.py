"""Mapbox Directions API wrapper — fetch walking routes with alternatives."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from shapely.geometry import LineString, mapping

logger = logging.getLogger(__name__)

MAPBOX_DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox/walking"


@dataclass
class RouteCandidate:
    geometry: LineString
    duration_seconds: float
    distance_meters: float


def _decode_polyline6(coords: list[list[float]]) -> LineString:
    """Mapbox returns GeoJSON coordinates [lon, lat]."""
    return LineString(coords)


def fetch_walking_routes(
    origin: tuple[float, float],
    dest: tuple[float, float],
    access_token: str,
    alternatives: bool = True,
    max_alternatives: int = 2,
) -> list[RouteCandidate]:
    """
    Fetch default + alternative walking routes from Mapbox Directions.

    origin/dest are (lon, lat).
    """
    if not access_token:
        logger.warning("No Mapbox token — returning synthetic straight-line route")
        return _synthetic_routes(origin, dest)

    coords = f"{origin[0]},{origin[1]};{dest[0]},{dest[1]}"
    params: dict[str, str | int | bool] = {
        "access_token": access_token,
        "geometries": "geojson",
        "overview": "full",
        "alternatives": alternatives,
        "steps": False,
    }

    url = f"{MAPBOX_DIRECTIONS_URL}/{coords}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != "Ok":
        raise ValueError(f"Mapbox Directions error: {data.get('code', 'unknown')}")

    routes: list[RouteCandidate] = []
    for route in data.get("routes", [])[: max_alternatives + 1]:
        geom_coords = route["geometry"]["coordinates"]
        routes.append(
            RouteCandidate(
                geometry=_decode_polyline6(geom_coords),
                duration_seconds=float(route["duration"]),
                distance_meters=float(route["distance"]),
            )
        )

    return routes or _synthetic_routes(origin, dest)


def _synthetic_routes(
    origin: tuple[float, float],
    dest: tuple[float, float],
) -> list[RouteCandidate]:
    """Fallback when Mapbox is unavailable — straight line + slight offset."""
    direct = LineString([origin, dest])
    mid = (
        (origin[0] + dest[0]) / 2 + 0.002,
        (origin[1] + dest[1]) / 2 - 0.001,
    )
    alt = LineString([origin, mid, dest])

    def estimate_duration(line: LineString) -> float:
        # Rough walking speed ~1.4 m/s; 1 deg lat ~ 111km
        length_deg = line.length
        meters = length_deg * 111_000
        return meters / 1.4

    return [
        RouteCandidate(
            geometry=direct,
            duration_seconds=estimate_duration(direct),
            distance_meters=direct.length * 111_000,
        ),
        RouteCandidate(
            geometry=alt,
            duration_seconds=estimate_duration(alt) * 1.1,
            distance_meters=alt.length * 111_000,
        ),
    ]


def route_to_geojson(
    segments: list[dict],
    route_score: float,
) -> dict:
    """Build a FeatureCollection with per-segment risk for map styling."""
    features = []
    for i, seg in enumerate(segments):
        geom = seg.get("geometry")
        if geom is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "segment_id": seg.get("segment_id"),
                    "risk": seg.get("risk", 0.0),
                    "sidewalk_cov": seg.get("sidewalk_cov"),
                    "traffic_risk": seg.get("traffic_risk"),
                    "index": i,
                },
                "geometry": mapping(geom) if hasattr(geom, "__geo_interface__") else geom,
            }
        )

    return {
        "type": "FeatureCollection",
        "properties": {"route_score": route_score},
        "features": features,
    }
