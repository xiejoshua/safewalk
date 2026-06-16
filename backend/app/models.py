from typing import Literal

from pydantic import BaseModel, Field


ProfileName = Literal["day", "night", "accessible"]


class ScoreRequest(BaseModel):
    origin: tuple[float, float] = Field(
        ...,
        description="Origin [lon, lat] — typically a MARTA stop",
        examples=[[-84.347, 33.610]],
    )
    dest: tuple[float, float] = Field(
        ...,
        description="Destination [lon, lat]",
        examples=[[-84.329, 33.620]],
    )
    weights: dict[str, float] | None = Field(
        default=None,
        description="Optional factor weights; normalized to sum 1. Overrides profile.",
    )
    profile: ProfileName | None = Field(
        default="day",
        description="Preset weight profile when weights are omitted",
    )


class RouteResult(BaseModel):
    score: float
    minutes: float
    geojson: dict
    explanation: str | None = None


class ScoreResponse(BaseModel):
    safest: RouteResult
    alternatives: list[RouteResult]


class HealthResponse(BaseModel):
    status: str


class RouteSegment(BaseModel):
    segment_id: str
    sidewalk_cov: float
    traffic_risk: float
    crash_norm: float
    hazard_norm: float
    canopy_pct: float
    exposure_norm: float
    slope_risk: float
    length_m: float
    geometry: dict | None = None
    risk: float | None = None


class SafeRouteResult(BaseModel):
    segments: list[RouteSegment]
    total_risk: float
    distance_m: float
    explanation: str


class FastRouteResult(BaseModel):
    segments: list[RouteSegment]
    distance_m: float


class RouteResponse(BaseModel):
    safe_route: SafeRouteResult
    fast_route: FastRouteResult


class GapReport(BaseModel):
    id: str
    type: str
    note: str | None = None
    photo_url: str | None = None
    lng: float | None = None
    lat: float | None = None
    status: str | None = None
    reported_at: str | None = None


class VerifyGapResponse(BaseModel):
    verified: bool
    confidence: float | None = None
    report: GapReport | None = None
    reason: str | None = None
    ai_type: str | None = None


class SegmentDetailResponse(BaseModel):
    segment_id: str
    sidewalk_cov: float
    traffic_risk: float
    crash_norm: float
    hazard_norm: float
    canopy_pct: float
    exposure_norm: float
    slope_risk: float
    composite_score: float
    geometry: dict | None = None
