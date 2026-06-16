"""API route handlers."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.directions import fetch_walking_routes, route_to_geojson
from app.gap_reports import (
    GapReportError,
    list_gap_reports,
    update_gap_report_status,
    verify_and_record_gap,
)
from app.models import (
    FastRouteResult,
    GapReport,
    GapStatusUpdate,
    HealthResponse,
    RouteResponse,
    RouteResult,
    RouteSegment,
    SafeRouteResult,
    ScoreRequest,
    ScoreResponse,
    SegmentDetailResponse,
    VerifyGapResponse,
)
from app.network import GraphRouter, serialize_segment
from app.scoring import (
    build_explanation,
    resolve_weights_from_sliders,
    score_route,
)
from app.segment_repository import SegmentRepository

logger = logging.getLogger(__name__)

router = APIRouter()

SIDEWALK_SERVICE_URL = (
    "https://services2.arcgis.com/zLeajbicrDRLQcny/ArcGIS/rest/services/"
    "Sidewalks_Inventory/FeatureServer/2/query"
)
MARTA_AREA_ENVELOPE = "-84.52,33.61,-84.20,33.97"
SIDEWALK_PAGE_SIZE = 2000


def sidewalk_quality(sidewalk_cov: float | None) -> str:
    if sidewalk_cov is None:
        return "partial"
    if sidewalk_cov >= 0.75:
        return "full"
    if sidewalk_cov >= 0.25:
        return "partial"
    return "none"


def sidewalk_inventory_quality(properties: dict) -> str:
    rating = str(properties.get("SWCIRating", "")).lower()
    sidewalk_type = str(properties.get("SidewalkType", "")).lower()
    condition = str(properties.get("ObservedCondition", "")).lower()

    if "no sidewalk" in rating or "no sw" in sidewalk_type or "no sw" in condition:
        return "none"
    if "excellent" in rating or "good" in rating:
        return "full"
    if "fair" in rating or "poor" in rating:
        return "partial"
    return "full"


def fetch_arc_sidewalks() -> dict:
    features = []

    try:
        with httpx.Client(timeout=12) as client:
            for offset in range(0, 20000, SIDEWALK_PAGE_SIZE):
                response = client.get(
                    SIDEWALK_SERVICE_URL,
                    params={
                        "f": "geojson",
                        "where": "1=1",
                        "outFields": "OBJECTID,SW_ID,StreetName,SidewalkType,ObservedCondition,SWCIRating",
                        "returnGeometry": "true",
                        "outSR": "4326",
                        "geometry": MARTA_AREA_ENVELOPE,
                        "geometryType": "esriGeometryEnvelope",
                        "inSR": "4326",
                        "spatialRel": "esriSpatialRelIntersects",
                        "resultRecordCount": str(SIDEWALK_PAGE_SIZE),
                        "resultOffset": str(offset),
                    },
                )
                response.raise_for_status()
                page_features = response.json().get("features", [])

                for feature in page_features:
                    if feature.get("geometry", {}).get("type") != "LineString":
                        continue
                    properties = feature.get("properties") or {}
                    properties["quality"] = sidewalk_inventory_quality(properties)
                    if properties["quality"] == "none":
                        continue
                    feature["properties"] = properties
                    features.append(feature)

                if len(page_features) < SIDEWALK_PAGE_SIZE:
                    break
    except Exception:
        logger.exception("ARC sidewalk fetch failed")
        return {"type": "FeatureCollection", "features": []}

    return {"type": "FeatureCollection", "features": features}


# =========================================================================
# POST /score — Mapbox candidate scoring
# =========================================================================

def score_routes(request: ScoreRequest, http_request: Request) -> ScoreResponse:
    """Core scoring logic — runs in threadpool (sync handler)."""
    settings = http_request.app.state.settings
    segment_store = http_request.app.state.segment_store

    weights = resolve_weights_from_sliders(
        sidewalks=request.sidewalks,
        safety=request.safety,
        comfort=request.comfort,
        theme=request.theme,
    )
    step_free = request.step_free

    try:
        candidates = fetch_walking_routes(
            origin=request.origin,
            dest=request.dest,
            access_token=settings.mapbox_access_token,
        )
    except Exception as exc:
        logger.exception("Mapbox Directions failed")
        raise HTTPException(status_code=502, detail=f"Directions API error: {exc}") from exc

    scored: list[RouteResult] = []
    for candidate in candidates:
        segments = segment_store.snap_route(candidate.geometry, weights, step_free=step_free)
        route_score = score_route(segments, weights, step_free=step_free)
        geojson = route_to_geojson(segments, route_score)
        explanation = build_explanation(segments, weights, step_free=step_free)

        scored.append(
            RouteResult(
                score=round(route_score, 4) if route_score != float("inf") else 9999.0,
                minutes=round(candidate.duration_seconds / 60.0, 1),
                geojson=geojson,
                explanation=explanation,
            )
        )

    scored.sort(key=lambda r: r.score)

    if not scored:
        raise HTTPException(status_code=404, detail="No routes found")

    safest = scored[0]
    alternatives = scored[1:]

    return ScoreResponse(safest=safest, alternatives=alternatives)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/api/sidewalks/")
def get_sidewalks(http_request: Request) -> dict:
    segment_store = http_request.app.state.segment_store
    gdf = segment_store.gdf

    if gdf.empty:
        return fetch_arc_sidewalks()

    sidewalks = gdf.to_crs(4326)
    features = []
    for _, row in sidewalks.iterrows():
        geometry = row.geometry
        if geometry.geom_type != "LineString":
            continue
        quality = sidewalk_quality(row.get("sidewalk_cov"))
        if quality == "none":
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "quality": quality,
                },
                "geometry": geometry.__geo_interface__,
            }
        )

    return {"type": "FeatureCollection", "features": features}


@router.post("/score", response_model=ScoreResponse)
def post_score(request: ScoreRequest, http_request: Request) -> ScoreResponse:
    return score_routes(request, http_request)


# =========================================================================
# GET /route — Dijkstra walkable-graph routing
# =========================================================================

def _to_route_segments(segments: list[dict], include_risk: bool) -> list[RouteSegment]:
    output: list[RouteSegment] = []
    for seg in segments:
        serialized = serialize_segment(seg, risk=seg.get("risk") if include_risk else None)
        output.append(RouteSegment(**serialized))
    return output


@router.get("/route", response_model=RouteResponse)
def get_route(
    http_request: Request,
    origin_lat: float = Query(..., ge=-90, le=90),
    origin_lng: float = Query(..., ge=-180, le=180),
    dest_lat: float = Query(..., ge=-90, le=90),
    dest_lng: float = Query(..., ge=-180, le=180),
    sidewalks: int | None = Query(default=None, ge=0, le=100),
    safety:    int | None = Query(default=None, ge=0, le=100),
    comfort:   int | None = Query(default=None, ge=0, le=100),
    step_free: bool = Query(default=False),
    theme: Literal["light", "dark"] = Query(default="light"),
) -> RouteResponse:
    graph: GraphRouter = http_request.app.state.graph_router
    if graph.walkable_gdf.empty:
        raise HTTPException(status_code=503, detail="Walkable network is not loaded")

    weights = resolve_weights_from_sliders(
        sidewalks=sidewalks, safety=safety, comfort=comfort, theme=theme,
    )

    try:
        safe_segments, fast_segments, mean_risk, safe_distance, fast_distance, _ = graph.route(
            origin_lon=origin_lng,
            origin_lat=origin_lat,
            dest_lon=dest_lng,
            dest_lat=dest_lat,
            weights=weights,
            step_free=step_free,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    explanation = build_explanation(safe_segments, weights, step_free=step_free)

    return RouteResponse(
        safe_route=SafeRouteResult(
            segments=_to_route_segments(safe_segments, include_risk=True),
            total_risk=round(mean_risk, 6),
            distance_m=round(safe_distance, 2),
            explanation=explanation,
        ),
        fast_route=FastRouteResult(
            segments=_to_route_segments(fast_segments, include_risk=True),
            distance_m=round(fast_distance, 2),
        ),
    )


@router.get("/gap-reports", response_model=list[GapReport])
async def get_gap_reports() -> list[GapReport]:
    """List all crowdsourced gap reports (the pins shown on the map)."""
    try:
        rows = await run_in_threadpool(list_gap_reports)
    except GapReportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface upstream failures as 502
        logger.exception("gap-reports list failed")
        raise HTTPException(status_code=502, detail=f"gap_reports read error: {exc}") from exc

    return [GapReport(**row) for row in rows]


@router.patch("/gap-reports/{report_id}", response_model=GapReport)
async def patch_gap_report(report_id: str, update: GapStatusUpdate) -> GapReport:
    """Update a report's workflow status (reported -> in_progress -> processed)."""
    try:
        row = await run_in_threadpool(update_gap_report_status, report_id, update.status)
    except GapReportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("gap-report status update failed")
        raise HTTPException(status_code=502, detail=f"gap_reports update error: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail=f"Gap report not found: {report_id}")
    return GapReport(**row)


@router.post("/verify-gap", response_model=VerifyGapResponse)
async def verify_gap(
    photo: UploadFile = File(...),
    lng: float = Form(..., ge=-180, le=180),
    lat: float = Form(..., ge=-90, le=90),
    note: str = Form(""),
) -> VerifyGapResponse:
    """Verify a crowdsourced gap photo with Claude vision; if real, store it as a pin.

    Verified reports are inserted into Supabase gap_reports, which the frontend map
    subscribes to over realtime — so a confirmed pin appears live on every client.
    """
    image_bytes = await photo.read()
    media_type = photo.content_type or "image/jpeg"

    try:
        result = await run_in_threadpool(
            verify_and_record_gap, image_bytes, media_type, lng, lat, note
        )
    except GapReportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface upstream failures as 502
        logger.exception("verify-gap failed")
        raise HTTPException(status_code=502, detail=f"Gap verification error: {exc}") from exc

    return VerifyGapResponse(**result)


@router.get("/segment/{segment_id}", response_model=SegmentDetailResponse)
def get_segment(segment_id: str, http_request: Request) -> SegmentDetailResponse:
    repo: SegmentRepository = http_request.app.state.segment_repository
    try:
        data = repo.get_segment(segment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Segment not found: {segment_id}") from exc

    return SegmentDetailResponse(**data)
