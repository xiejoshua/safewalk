"""Safety scoring: weight resolution, segment risk, route aggregation."""

from __future__ import annotations

from typing import Any

FACTORS = ("sidewalk", "traffic", "crash", "hazards", "shade", "exposure", "slope")

DEFAULTS: dict[str, float] = {
    "sidewalk": 0.30,
    "traffic": 0.20,
    "crash": 0.20,
    "hazards": 0.15,
    "shade": 0.10,
    "exposure": 0.05,
    "slope": 0.00,
}

PROFILES: dict[str, dict[str, float]] = {
    "day": DEFAULTS,
    "night": {
        "sidewalk": 0.20,
        "traffic": 0.25,
        "crash": 0.30,
        "hazards": 0.15,
        "shade": 0.05,
        "exposure": 0.05,
        "slope": 0.00,
    },
    "accessible": {
        "sidewalk": 0.35,
        "traffic": 0.10,
        "crash": 0.10,
        "hazards": 0.15,
        "shade": 0.05,
        "exposure": 0.05,
        "slope": 0.20,
    },
}

# Column mapping: weight key -> parquet column
FACTOR_COLUMNS: dict[str, str] = {
    "sidewalk": "sidewalk_cov",
    "traffic": "traffic_risk",
    "crash": "crash_norm",
    "hazards": "hazard_norm",
    "shade": "canopy_pct",
    "exposure": "exposure_norm",
    "slope": "slope_risk",
}


def resolve_weights(
    weights: dict[str, float] | None,
    profile: str | None,
) -> dict[str, float]:
    """Clamp, ignore unknown keys, normalize to sum 1."""
    raw = weights or PROFILES.get(profile or "day", DEFAULTS)
    clamped = {k: max(0.0, float(raw.get(k, 0))) for k in FACTORS}
    total = sum(clamped.values()) or 1.0
    return {k: v / total for k, v in clamped.items()}


def _accessible_barrier(seg: dict[str, Any]) -> bool:
    """Hard-avoid segments with accessibility barriers."""
    if seg.get("barrier"):
        return True
    if seg.get("highway") == "steps":
        return True
    if seg.get("wheelchair") == "no":
        return True
    return False


def segment_risk(
    seg: dict[str, Any],
    weights: dict[str, float],
    profile: str,
    crossing_penalty: float = 0.0,
) -> float:
    """Per-segment weighted risk; inf for hard-avoids in accessible mode."""
    if profile == "accessible" and _accessible_barrier(seg):
        return float("inf")

    risk = (
        weights["sidewalk"] * (1.0 - float(seg.get("sidewalk_cov", 0.0)))
        + weights["traffic"] * float(seg.get("traffic_risk", 0.0))
        + weights["crash"] * float(seg.get("crash_norm", 0.0))
        + weights["hazards"] * float(seg.get("hazard_norm", 0.0))
        + weights["shade"] * (1.0 - float(seg.get("canopy_pct", 0.0)))
        + weights["exposure"] * float(seg.get("exposure_norm", 0.0))
        + weights["slope"] * float(seg.get("slope_risk") or 0.0)
        + crossing_penalty
    )
    return risk


def crossing_penalty(seg: dict[str, Any], profile: str) -> float:
    """Use R3 precomputed crossing_penalty; ×2.5 multiplier for accessible profile."""
    penalty = float(seg.get("crossing_penalty") or 0.0)
    if profile == "accessible" and penalty > 0:
        penalty *= 2.5
    return penalty


def score_route(
    segments: list[dict[str, Any]],
    weights: dict[str, float],
    profile: str,
) -> float:
    """Route score = mean segment risk; inf if any segment is a hard-avoid."""
    if not segments:
        return float("inf")

    risks: list[float] = []
    for seg in segments:
        cp = crossing_penalty(seg, profile)
        r = segment_risk(seg, weights, profile, crossing_penalty=cp)
        if r == float("inf"):
            return float("inf")
        risks.append(r)

    return sum(risks) / len(risks)


def build_explanation(
    segments: list[dict[str, Any]],
    weights: dict[str, float],
    profile: str,
) -> str:
    """Plain-language summary of why this route is safer."""
    if not segments:
        return "No route segments matched the safety network."

    totals: dict[str, float] = {k: 0.0 for k in FACTORS}
    n = len(segments)
    for seg in segments:
        totals["sidewalk"] += 1.0 - float(seg.get("sidewalk_cov", 0.0))
        totals["traffic"] += float(seg.get("traffic_risk", 0.0))
        totals["crash"] += float(seg.get("crash_norm", 0.0))
        totals["hazards"] += float(seg.get("hazard_norm", 0.0))
        totals["shade"] += 1.0 - float(seg.get("canopy_pct", 0.0))
        totals["exposure"] += float(seg.get("exposure_norm", 0.0))
        totals["slope"] += float(seg.get("slope_risk") or 0.0)

    weighted = {k: totals[k] * weights[k] / n for k in FACTORS}
    top = max(weighted, key=weighted.get)  # type: ignore[arg-type]

    reasons = {
        "sidewalk": "more continuous sidewalk coverage",
        "traffic": "lower exposure to fast, high-volume traffic",
        "crash": "fewer crash hotspots along the path",
        "hazards": "fewer reported hazards nearby",
        "shade": "more tree shade along the walk",
        "exposure": "lower heat and pollution exposure",
        "slope": "gentler grades for easier walking",
    }

    profile_note = ""
    if profile == "night":
        profile_note = " (optimized for night walking)"
    elif profile == "accessible":
        profile_note = " (avoids steps and steep grades)"

    pct_no_sidewalk = sum(1.0 - float(s.get("sidewalk_cov", 0.0)) for s in segments) / n
    sidewalk_pct = (1.0 - pct_no_sidewalk) * 100

    return (
        f"This route prioritizes {reasons[top]}{profile_note}. "
        f"About {sidewalk_pct:.0f}% of the path has sidewalk coverage."
    )
