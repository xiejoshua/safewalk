from typing import Literal

from pydantic import BaseModel, Field


Theme = Literal["light", "dark"]


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
    sidewalks: int | None = Field(
        default=None, ge=0, le=100,
        description="Sidewalk-presence weight (0–100). Theme default used if omitted.",
    )
    safety: int | None = Field(
        default=None, ge=0, le=100,
        description="Safety blend weight (traffic + crash + hazards + flooding).",
    )
    comfort: int | None = Field(
        default=None, ge=0, le=100,
        description="Comfort blend weight (shade + heat + slope).",
    )
    step_free: bool = Field(
        default=False,
        description="Hard-avoid stairs / wheelchair=no / steep grades.",
    )
    theme: Theme = Field(
        default="light",
        description="Light = day defaults, dark = night defaults. Used as fallback for omitted sliders.",
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
