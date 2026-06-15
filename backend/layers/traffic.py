"""
layers/traffic.py — emit traffic_risk ∈ [0, 1] per segment.

Three input signals, weighted-summed:
  - OSM `highway` class (most reliable, no NaN)         weight W_CLASS = 0.40
  - OSM `maxspeed` step-function at 35 / 45 mph breaks  weight W_SPEED = 0.30
  - GDOT AADT non-linear sigmoid (saturates ≥30k, ≈0 below 5k)
    propagated from point stations along osm_way_id       weight W_AADT  = 0.30

Missing inputs fall back to per-class defaults documented inline. Component
weights and lookup tables are at the top of the file so they're easy to audit
or tune.

The `_parse_maxspeed_mph` and `_parse_lanes_count` helpers double as the
numeric coercion R2 needs in `prebake.py` (per the integration audit Hard #1);
expose them publicly so `prebake.py` can reuse them.

Refresh the AADT cache:
  curl -sL "https://services5.arcgis.com/buITjRsK0rZsAXbQ/arcgis/rest/services/GDOT_AADT_and_TruckPct_2008to2017/FeatureServer/0/query?where=1=1&geometry=-84.37,33.58,-84.29,33.65&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=Station_ID,Functional_Class,AADT_2017&outSR=4326&f=geojson" \
    -o data/gdot/aadt_2017.geojson
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd

# Three .parent hops: layers/traffic.py → layers/ → backend/ → repo root.
REPO = Path(__file__).resolve().parent.parent.parent
AADT_PATH = REPO / "data" / "gdot" / "aadt_2017.geojson"
AADT_FIELD = "AADT_2017"

WGS84 = "EPSG:4326"
UTM16N = "EPSG:32616"

# --- Class → base risk (TASKS.md line 30) ---------------------------------
# Order: safest → most dangerous. Pedestrian classes are 0; service/residential
# are low; arterials climb up; primary tops the table (45+ mph multi-lane).
CLASS_BASE_RISK = {
    "footway":        0.00,
    "path":           0.00,
    "pedestrian":     0.00,
    "steps":          0.00,
    "living_street":  0.10,
    "service":        0.15,
    "residential":    0.25,
    "unclassified":   0.30,
    "tertiary":       0.50,
    "tertiary_link":  0.50,
    "secondary":      0.70,
    "secondary_link": 0.70,
    "primary":        0.85,
    "primary_link":   0.85,
}
CLASS_BASE_RISK_DEFAULT = 0.40

# --- maxspeed → step factor (TASKS.md line 31) ----------------------------
# Three regions at the 35 / 45 mph breaks. A 35 mph street is qualitatively
# different from 25; 45+ is another jump (per spec).
SPEED_FACTOR_LOW = 0.20    # < 35 mph
SPEED_FACTOR_MID = 0.60    # 35–44 mph
SPEED_FACTOR_HIGH = 1.00   # ≥ 45 mph

# Default speed risk when `maxspeed` is missing (TASKS.md line 32).
# Primary roads in urban Atlanta have signals every few blocks and typically
# run 35-40 mph, not 45+; defaulting to 1.0 over-scored Jonesboro Rd in the
# 2026-06-15 spot-check (pick #7). Dropped to 0.80 (high-MID territory).
CLASS_SPEED_DEFAULT = {
    "footway": 0.0, "path": 0.0, "pedestrian": 0.0, "steps": 0.0,
    "living_street": 0.0, "service": 0.0,
    "residential": 0.20, "unclassified": 0.20,
    "tertiary": 0.60, "tertiary_link": 0.60,
    "secondary": 0.60, "secondary_link": 0.60,
    "primary": 0.80, "primary_link": 0.80,
}
CLASS_SPEED_DEFAULT_FALLBACK = 0.40

# --- AADT-missing fallback (TASKS.md line 32) ----------------------------
CLASS_AADT_DEFAULT = {
    "footway": 0.0, "path": 0.0, "pedestrian": 0.0, "steps": 0.0,
    "living_street": 0.0, "service": 0.05,
    "residential": 0.15, "unclassified": 0.20,
    "tertiary": 0.50, "tertiary_link": 0.50,
    "secondary": 0.70, "secondary_link": 0.70,
    "primary": 0.90, "primary_link": 0.90,
}
CLASS_AADT_DEFAULT_FALLBACK = 0.40

# --- Component weights ----------------------------------------------------
W_CLASS = 0.40
W_SPEED = 0.30
W_AADT = 0.30

# AADT sigmoid output is capped at this value (see _aadt_factor docstring).
AADT_FACTOR_CAP = 0.80

# --- AADT snap distance (m) ----------------------------------------------
# Stations are snapped to the nearest segment within this radius in EPSG:32616.
# 50 m catches stations sited at the curb or median of the way they measure.
AADT_SNAP_M = 50.0


# =========================================================================
# Helpers — string → numeric coercion (also used by prebake.py per Hard #1)
# =========================================================================

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _str(v) -> str:
    """Coerce None / NaN / non-strings to '' for robust tag lookup."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return str(v).lower()


def _parse_maxspeed_mph(v) -> float | None:
    """Coerce an OSM `maxspeed` tag to mph.

    Handles:
      - numeric (already a float):  35.0       → 35.0
      - bare number:                "35"       → 35.0
      - with unit:                  "35 mph"   → 35.0
      - km/h unit:                  "50 km/h"  → 31.07
      - split:                      "35;40"    → 40.0 (worst case)
      - non-numeric:                "signals", "walk", "none", "variable" → None
    """
    if v is None:
        return None
    if isinstance(v, (int, float)) and not (isinstance(v, float) and v != v):
        return float(v)
    s = str(v).strip().lower()
    if not s:
        return None
    nums = _NUM_RE.findall(s)
    if not nums:
        return None
    val = max(float(n) for n in nums)
    if "km" in s:
        val = val * 0.6213711922
    return val


def _parse_lanes_count(v) -> float | None:
    """Coerce an OSM `lanes` or `width` tag to a numeric count / meters.

    Handles bare numbers, "2;3" splits (take the max), and non-numeric tokens.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)) and not (isinstance(v, float) and v != v):
        return float(v)
    s = str(v).strip().lower()
    if not s:
        return None
    nums = _NUM_RE.findall(s)
    if not nums:
        return None
    return max(float(n) for n in nums)


# =========================================================================
# Component scorers
# =========================================================================

def _speed_factor(mph: float | None, highway: str) -> float:
    """Step function at 35 / 45 mph breaks; class default when maxspeed missing.

    `pd.isna` catches both `None` and `NaN` — Series `.apply` propagates missing
    values as NaN even when the producer returns None.
    """
    if mph is None or pd.isna(mph):
        return CLASS_SPEED_DEFAULT.get(highway, CLASS_SPEED_DEFAULT_FALLBACK)
    if mph >= 45.0:
        return SPEED_FACTOR_HIGH
    if mph >= 35.0:
        return SPEED_FACTOR_MID
    return SPEED_FACTOR_LOW


def _aadt_factor(aadt) -> float | None:
    """Sigmoid risk curve: ≈0 below 5k AADT, 0.5 at 22.5k, capped at 0.80.

    Capped at 0.80 (not 1.0) because at very high AADT, real pedestrian risk
    is mitigated by road-design factors we don't observe — signals, refuge
    medians, dedicated sidewalks. Saturating to 1.0 over-scored Jonesboro Rd
    (AADT 27,100) in the 2026-06-15 spot-check (pick #7); the user read 0.6
    while the algorithm read 0.92.

    Midpoint shifted from 17.5k → 22.5k, slope softened from 4k → 5k. New
    behavior:
      - 5k AADT  → ~0.030
      - 15k     → ~0.182
      - 22.5k   → 0.500
      - 27k     → 0.711
      - 30k     → 0.800 (cap)
      - 35k+    → 0.800 (cap, hard)

    Returns None when AADT is missing — caller falls back to class default.
    """
    if aadt is None or pd.isna(aadt):
        return None
    a = float(aadt)
    if a <= 0.0:
        return 0.0
    if a >= 35_000.0:
        return AADT_FACTOR_CAP
    x = (a - 22_500.0) / 5_000.0
    raw = 1.0 / (1.0 + math.exp(-x))
    return min(raw, AADT_FACTOR_CAP)


# =========================================================================
# AADT loading + spatial snap
# =========================================================================

def _load_aadt_stations(path: Path = AADT_PATH) -> gpd.GeoDataFrame:
    """Load AADT station points; ensure WGS84 CRS."""
    if not path.exists():
        raise FileNotFoundError(
            f"AADT cache not found: {path}. "
            f"See the module docstring for the refresh command."
        )
    g = gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs(WGS84)
    return g


def _snap_aadt_to_ways(
    segments: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
    snap_m: float = AADT_SNAP_M,
) -> pd.Series:
    """Spatial-snap stations to the nearest segment within `snap_m` (EPSG:32616),
    then propagate AADT to every segment sharing the matched `osm_way_id`.

    For ways with multiple stations, take the **max** AADT (worst-case for safety).
    Returns a float64 Series indexed by `segments.index`, with NaN where no
    station was within range of the parent way.
    """
    if stations.empty or segments.empty or AADT_FIELD not in stations.columns:
        return pd.Series(float("nan"), index=segments.index, dtype=float)

    segs_utm = segments[["osm_way_id", "geometry"]].to_crs(UTM16N).copy()
    segs_utm = segs_utm.reset_index().rename(columns={"index": "_seg_idx"})

    stns_utm = stations[[AADT_FIELD, "geometry"]].to_crs(UTM16N).copy()
    # Drop stations with missing or zero AADT — they're useless and would
    # contaminate the max() aggregation.
    stns_utm = stns_utm[stns_utm[AADT_FIELD].fillna(0) > 0].copy()
    if stns_utm.empty:
        return pd.Series(float("nan"), index=segments.index, dtype=float)

    snapped = gpd.sjoin_nearest(
        stns_utm, segs_utm, how="left", max_distance=snap_m, distance_col="_dist_m"
    )
    snapped = snapped.dropna(subset=["osm_way_id"])
    if snapped.empty:
        return pd.Series(float("nan"), index=segments.index, dtype=float)

    way_aadt = snapped.groupby("osm_way_id")[AADT_FIELD].max()
    out = segments["osm_way_id"].map(way_aadt).astype(float)
    out.index = segments.index
    return out


# =========================================================================
# Public scorer
# =========================================================================

def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return traffic_risk ∈ [0, 1] per segment_id.

    Combination: 0.40 * class_risk + 0.30 * speed_risk + 0.30 * aadt_risk,
    clipped to [0, 1].
    """
    if segments.empty:
        return pd.Series([], dtype=float, name="traffic_risk")

    highways = segments["highway"].astype("object").apply(_str)

    class_risk = highways.map(CLASS_BASE_RISK).fillna(CLASS_BASE_RISK_DEFAULT)

    parsed_mph = segments["maxspeed"].apply(_parse_maxspeed_mph)
    speed_risk = pd.Series(
        [_speed_factor(m, hw) for m, hw in zip(parsed_mph, highways)],
        index=segments.index,
        dtype=float,
    )

    aadt = _snap_aadt_to_ways(segments, _load_aadt_stations())
    aadt_factor_series = aadt.apply(_aadt_factor)
    class_aadt_default = highways.map(CLASS_AADT_DEFAULT).fillna(CLASS_AADT_DEFAULT_FALLBACK)
    aadt_risk = aadt_factor_series.where(aadt_factor_series.notna(), class_aadt_default)

    risk = W_CLASS * class_risk + W_SPEED * speed_risk + W_AADT * aadt_risk
    risk = risk.clip(0.0, 1.0).astype(float)
    risk.name = "traffic_risk"
    return risk
