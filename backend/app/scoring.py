"""Safety scoring: 3 sliders + step-free toggle → weighted segment risk.

Public API (used by routes.py, network.py, segments.py, segment_repository.py):
  - resolve_weights_from_sliders(sidewalks, safety, comfort, theme)
        → dict[str, float] of 8 normalized factor weights
  - segment_risk(seg, weights, step_free, crossing_penalty_value)
  - crossing_penalty(seg, step_free)
  - score_route(segments, weights, step_free)
  - build_explanation(segments, weights, step_free)

Design (locked 2026-06-16, see SCORING.md):
  Frontend exposes 3 sliders (Sidewalks / Safety / Comfort) and 1 toggle
  (step-free / wheelchair-accessible). Each slider value is 0–100. Light vs
  dark theme picks defaults; user-adjusted sliders override. The toggle
  controls a hard-avoid: when on, `risk = inf` for any segment with
  `barrier == True`.

Sub-weight blends (within each slider):
  Sidewalks  → 100% sidewalk
  Safety     →  40% traffic + 35% crash + 15% hazards + 10% flooding
  Comfort    →  40% shade (1 − canopy_pct) + 25% heat + 35% slope
Crossing_penalty stays as a flat additive outside the weighted sum.
"""

from __future__ import annotations

from typing import Any, Literal

# 8 weighted factors. Order matches FACTOR_COLUMNS below.
FACTORS: tuple[str, ...] = (
    "sidewalk", "traffic", "crash", "hazards",
    "shade", "exposure", "slope", "flooding",
)

# Parquet column name for each weight key (used by build_explanation).
FACTOR_COLUMNS: dict[str, str] = {
    "sidewalk": "sidewalk_cov",
    "traffic":  "traffic_risk",
    "crash":    "crash_norm",
    "hazards":  "hazard_norm",
    "shade":    "canopy_pct",
    "exposure": "exposure_norm",
    "slope":    "slope_risk",
    "flooding": "flooding",
}

# =========================================================================
# Slider model — 3 sliders + theme defaults
# =========================================================================

Theme = Literal["light", "dark"]

# Slider defaults per theme. Each pair sums to 100 — matches DESIGN.md §7c.
SLIDER_DEFAULTS: dict[Theme, dict[str, int]] = {
    "light": {"sidewalks": 30, "safety": 55, "comfort": 15},
    "dark":  {"sidewalks": 20, "safety": 70, "comfort": 10},
}

# Sub-weight allocation within each slider. Sums per slider == 1.0.
# Crossing penalty is added flat outside the weighted sum (see segment_risk).
# Sub-weights within each slider. Each row sums to 1.0.
# Safety sub-weights tuned to match the data distribution: crash_norm,
# hazard_norm, and flooding are sparse (>99% of segments score 0) in the
# current parquet; only traffic_risk varies meaningfully. Original
# 40/35/15/10 allocation made safety-heavy LOWER mean risk than light
# defaults (paradoxical). Rebalanced to 65/20/10/5 — traffic dominates,
# others kept non-zero for forward-compat when R4 broadens data coverage.
SUBWEIGHTS: dict[str, dict[str, float]] = {
    "sidewalks": {"sidewalk":  1.00},
    "safety":    {"traffic":   0.65, "crash":    0.20, "hazards": 0.10, "flooding": 0.05},
    "comfort":   {"shade":     0.40, "exposure": 0.25, "slope":   0.35},
}


def resolve_weights_from_sliders(
    sidewalks: float | None = None,
    safety: float | None = None,
    comfort: float | None = None,
    theme: Theme = "light",
) -> dict[str, float]:
    """Translate 3 slider values (0–100 each) into 7 factor weights (sum=1).

    Any slider passed as None falls back to ``SLIDER_DEFAULTS[theme]``. This
    means callers can send only the sliders the user touched and rely on
    theme defaults for the rest.

    Algorithm:
      1. Fill missing sliders with theme defaults.
      2. Normalize the 3 slider values to sum to 1.
      3. For each slider, distribute its normalized weight across factors
         using SUBWEIGHTS.
      4. Sum per-factor across all sliders.

    Returns a dict keyed by every name in FACTORS, with weights summing to 1.0.
    """
    defaults = SLIDER_DEFAULTS[theme]
    raw = {
        "sidewalks": float(sidewalks if sidewalks is not None else defaults["sidewalks"]),
        "safety":    float(safety    if safety    is not None else defaults["safety"]),
        "comfort":   float(comfort   if comfort   is not None else defaults["comfort"]),
    }
    # Clamp negative → 0; normalize 3 sliders to sum=1.
    raw = {k: max(0.0, v) for k, v in raw.items()}
    total = sum(raw.values()) or 1.0
    norm = {k: v / total for k, v in raw.items()}

    out: dict[str, float] = {f: 0.0 for f in FACTORS}
    for slider, slider_w in norm.items():
        for factor, sub_w in SUBWEIGHTS[slider].items():
            out[factor] += slider_w * sub_w
    return out


# =========================================================================
# Barrier hard-avoid (step-free toggle)
# =========================================================================

def _has_barrier(seg: dict[str, Any]) -> bool:
    """True iff this segment is impassable for step-free routing.

    Checks the unified `barrier` bool column first (set by prebake from
    crossing.py + slope.py). Falls back to individual OSM tags so the
    function works on segments that bypass the prebake pipeline.
    """
    if seg.get("barrier"):
        return True
    if seg.get("highway") == "steps":
        return True
    if seg.get("wheelchair") == "no":
        return True
    return False


# =========================================================================
# Risk computation
# =========================================================================

def crossing_penalty(seg: dict[str, Any], step_free: bool = False) -> float:
    """Use R3 precomputed crossing_penalty; ×2.5 multiplier when step_free is on.

    The ×2.5 honors the accessible-profile convention from DESIGN.md (crossings
    are dramatically more dangerous for wheelchair / stroller users).
    """
    penalty = float(seg.get("crossing_penalty") or 0.0)
    if step_free and penalty > 0:
        penalty *= 2.5
    return penalty


def segment_risk(
    seg: dict[str, Any],
    weights: dict[str, float],
    step_free: bool = False,
    crossing_penalty_value: float = 0.0,
) -> float:
    """Per-segment weighted risk in [0, 1]; inf when step_free is on AND segment has a barrier.

    The weighted sum is bounded to [0, 1] by construction (weights sum to 1,
    every factor is in [0, 1]). Adding the flat crossing_penalty (up to 0.225)
    can push the total above 1.0 in extreme slider configs, so we clamp the
    final result. Affects ~6 segments out of 30k under the most aggressive
    sidewalks-heavy config; nothing under realistic ones.
    """
    if step_free and _has_barrier(seg):
        return float("inf")

    risk = (
        weights["sidewalk"] * (1.0 - float(seg.get("sidewalk_cov", 0.0)))
        + weights["traffic"]  * float(seg.get("traffic_risk", 0.0))
        + weights["crash"]    * float(seg.get("crash_norm", 0.0))
        + weights["hazards"]  * float(seg.get("hazard_norm", 0.0))
        + weights["shade"]    * (1.0 - float(seg.get("canopy_pct", 0.0)))
        + weights["exposure"] * float(seg.get("exposure_norm", 0.0))
        + weights["slope"]    * float(seg.get("slope_risk") or 0.0)
        + weights["flooding"] * float(seg.get("flooding") or 0.0)
        + crossing_penalty_value
    )
    return min(1.0, max(0.0, risk))


def score_route(
    segments: list[dict[str, Any]],
    weights: dict[str, float],
    step_free: bool = False,
) -> float:
    """Route score = mean segment risk; inf if any segment is a hard-avoid."""
    if not segments:
        return float("inf")

    risks: list[float] = []
    for seg in segments:
        cp = crossing_penalty(seg, step_free)
        r = segment_risk(seg, weights, step_free, crossing_penalty_value=cp)
        if r == float("inf"):
            return float("inf")
        risks.append(r)
    return sum(risks) / len(risks)


# =========================================================================
# Explanation builder
# =========================================================================

_REASONS: dict[str, str] = {
    "sidewalk": "more continuous sidewalk coverage",
    "traffic":  "lower exposure to fast, high-volume traffic",
    "crash":    "fewer crash hotspots along the path",
    "hazards":  "fewer reported sidewalk hazards nearby",
    "shade":    "more tree shade along the walk",
    "exposure": "lower heat exposure",
    "slope":    "gentler grades for easier walking",
    "flooding": "avoidance of flood-prone segments",
}


def build_explanation(
    segments: list[dict[str, Any]],
    weights: dict[str, float],
    step_free: bool = False,
) -> str:
    """Plain-language summary: highlights the dominant contributing factor."""
    if not segments:
        return "No route segments matched the safety network."

    n = len(segments)
    totals: dict[str, float] = {f: 0.0 for f in FACTORS}
    for seg in segments:
        totals["sidewalk"] += 1.0 - float(seg.get("sidewalk_cov", 0.0))
        totals["traffic"]  += float(seg.get("traffic_risk", 0.0))
        totals["crash"]    += float(seg.get("crash_norm", 0.0))
        totals["hazards"]  += float(seg.get("hazard_norm", 0.0))
        totals["shade"]    += 1.0 - float(seg.get("canopy_pct", 0.0))
        totals["exposure"] += float(seg.get("exposure_norm", 0.0))
        totals["slope"]    += float(seg.get("slope_risk") or 0.0)
        totals["flooding"] += float(seg.get("flooding") or 0.0)

    weighted = {f: totals[f] * weights[f] / n for f in FACTORS}
    top = max(weighted, key=weighted.get)  # type: ignore[arg-type]

    pct_no_sidewalk = sum(1.0 - float(s.get("sidewalk_cov", 0.0)) for s in segments) / n
    sidewalk_pct = (1.0 - pct_no_sidewalk) * 100

    note = " (step-free routing)" if step_free else ""
    return (
        f"This route prioritizes {_REASONS[top]}{note}. "
        f"About {sidewalk_pct:.0f}% of the path has sidewalk coverage."
    )
