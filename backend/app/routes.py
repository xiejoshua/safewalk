"""API route handlers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.directions import fetch_walking_routes, route_to_geojson
from app.gap_reports import GapReportError, verify_and_record_gap
from app.models import (
    FastRouteResult,
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
from app.scoring import PROFILES, build_explanation, resolve_weights, score_route
from app.segment_repository import SegmentRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def score_routes(request: ScoreRequest, http_request: Request) -> ScoreResponse:
    """Core scoring logic — runs in threadpool (sync handler)."""
    settings = http_request.app.state.settings
    segment_store = http_request.app.state.segment_store

    weights = resolve_weights(request.weights, request.profile)
    profile = request.profile or "day"

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
        segments = segment_store.snap_route(candidate.geometry, weights, profile)
        route_score = score_route(segments, weights, profile)
        geojson = route_to_geojson(segments, route_score)
        explanation = build_explanation(segments, weights, profile)

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


@router.post("/score", response_model=ScoreResponse)
def post_score(request: ScoreRequest, http_request: Request) -> ScoreResponse:
    return score_routes(request, http_request)


def _route_weights(profile: str, sidewalk_weight: float, traffic_weight: float) -> dict[str, float]:
    base = dict(PROFILES.get(profile, PROFILES["day"]))
    base["sidewalk"] = sidewalk_weight
    base["traffic"] = traffic_weight
    return resolve_weights(base, None)


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
    profile: str = Query("day", pattern="^(day|night|accessible)$"),
    sidewalk_weight: float = Query(0.5, ge=0, le=1),
    traffic_weight: float = Query(0.2, ge=0, le=1),
) -> RouteResponse:
    graph: GraphRouter = http_request.app.state.graph_router
    if graph.walkable_gdf.empty:
        raise HTTPException(status_code=503, detail="Walkable network is not loaded")

    weights = _route_weights(profile, sidewalk_weight, traffic_weight)

    try:
        safe_segments, fast_segments, mean_risk, safe_distance, fast_distance, _ = graph.route(
            origin_lon=origin_lng,
            origin_lat=origin_lat,
            dest_lon=dest_lng,
            dest_lat=dest_lat,
            weights=weights,
            profile=profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    explanation = build_explanation(safe_segments, weights, profile)

    return RouteResponse(
        safe_route=SafeRouteResult(
            segments=_to_route_segments(safe_segments, include_risk=True),
            total_risk=round(mean_risk, 6),
            distance_m=round(safe_distance, 2),
            explanation=explanation,
        ),
        fast_route=FastRouteResult(
            segments=_to_route_segments(fast_segments, include_risk=False),
            distance_m=round(fast_distance, 2),
        ),
    )


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
