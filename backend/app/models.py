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
