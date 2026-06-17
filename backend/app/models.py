from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    display_score: float | None = None


class RouteSliderWeights(BaseModel):
    sidewalks: float
    safety: float
    comfort: float


class SafeRouteResult(BaseModel):
    segments: list[RouteSegment]
    total_risk: float
    distance_m: float
    explanation: str
    slider_weights: RouteSliderWeights


class FastRouteResult(BaseModel):
    segments: list[RouteSegment]
    distance_m: float
    slider_weights: RouteSliderWeights


class RouteResponse(BaseModel):
    safe_route: SafeRouteResult
    fast_route: FastRouteResult


class GapReport(BaseModel):
    # The live table's id may be an integer; coerce to string so the API is stable.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    id: str
    type: str
    note: str | None = None
    photo_url: str | None = None
    lng: float | None = None
    lat: float | None = None
    status: str | None = None
    reported_at: str | None = None


class GapReportCreate(BaseModel):
    type: str = "other"
    note: str | None = None
    lng: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-90, le=90)


class VerifyGapResponse(BaseModel):
    verified: bool
    confidence: float | None = None
    report: GapReport | None = None
    reason: str | None = None
    ai_type: str | None = None


class AnalyzeGapResponse(BaseModel):
    """Result of /analyze-gap — validity + suggested category, no DB write."""

    verified: bool
    # Suggested category when verified (used to pre-select the report form radio).
    type: str | None = None
    note: str | None = None
    confidence: float | None = None
    # Reason the photo was rejected (only when verified is False).
    reason: str | None = None
    ai_type: str | None = None


GapStatus = Literal["reported", "in_progress", "processed"]


class GapStatusUpdate(BaseModel):
    status: GapStatus


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
