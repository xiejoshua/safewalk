"""
layers/sidewalk.py — emit sidewalk_cov ∈ [0, 1] per segment.

Combines OSM sidewalk tags (already on each segment row) with the Clayton
County ARC sidewalk layer (cached at data/arc/clayton_sidewalks.geojson, 983
features inside the Gillem bbox).

Per TASKS.md §sidewalk null-handling:
  - Missing OSM tag ≠ "no sidewalk" — only count as 0 when the ARC layer
    also has no coverage in a small buffer.
  - The ARC layer is documented as "incomplete" — absence in the layer is
    "unknown," not "no." Positive OSM signals upgrade segments where ARC
    missed them.

Refresh the ARC cache:
  curl -sL "https://services5.arcgis.com/m528W8U8YDYeMPrQ/arcgis/rest/services/Sidewalks/FeatureServer/0/query?where=1=1&geometry=<W,S,E,N>&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&outSR=4326&f=geojson" \
    -o data/arc/clayton_sidewalks.geojson
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

# Three .parent hops: layers/sidewalk.py → layers/ → backend/ → repo root.
REPO = Path(__file__).resolve().parent.parent.parent
ARC_PATH = REPO / "data" / "arc" / "clayton_sidewalks.geojson"

WGS84 = "EPSG:4326"
UTM16N = "EPSG:32616"

# Buffer applied to the ARC sidewalk union before segment overlay.
# Captures the typical road-centerline-to-sidewalk-centerline offset on
# arterials (sidewalk ~2 m from curb, curb ~2-4 m from centerline).
# 6 m is a compromise: tight enough to avoid false positives across the
# street, loose enough to catch sidewalks on either side of an arterial.
ARC_BUFFER_M = 6.0

# Below this segment length, the ARC overlap fraction is unreliable —
# very short segments (tail merges, intersection geometry artifacts) can
# be fully covered by a curb return or driveway apron in the ARC layer
# and score 1.0 despite no real walkable sidewalk along their length.
# Surfaced by the 2026-06-15 spot-check, pick #5: a 4.8 m segment scored
# 1.0 from ARC alone with no sidewalk visible on Street View.
# Short segments fall back to the OSM signal alone (no ARC contribution).
MIN_SCORE_LENGTH_M = 8.0

_OSM_SIDEWALK_YES = {"yes", "both", "left", "right", "separate"}
_OSM_SIDEWALK_NO = {"no", "none"}
_OSM_PED_HIGHWAY = {"footway", "path", "pedestrian", "steps"}


def _str(v) -> str:
    """Coerce None / NaN / non-strings to '' for robust tag lookup."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN check (NaN != NaN)
        return ""
    return str(v).lower()


def _osm_signal(row) -> str:
    """Return 'yes' | 'no' | 'unknown' from OSM tag columns on a segment row."""
    hw = _str(row.get("highway"))
    if hw in _OSM_PED_HIGHWAY:
        return "yes"

    sw = _str(row.get("sidewalk"))
    sw_l = _str(row.get("sidewalk:left"))
    sw_r = _str(row.get("sidewalk:right"))
    fw = _str(row.get("footway"))

    if sw in _OSM_SIDEWALK_YES or sw_l in _OSM_SIDEWALK_YES or sw_r in _OSM_SIDEWALK_YES:
        return "yes"
    if fw:
        return "yes"
    if sw in _OSM_SIDEWALK_NO and sw_l in _OSM_SIDEWALK_NO and sw_r in _OSM_SIDEWALK_NO:
        return "no"
    if sw in _OSM_SIDEWALK_NO or sw_l in _OSM_SIDEWALK_NO or sw_r in _OSM_SIDEWALK_NO:
        return "no"
    return "unknown"


def _load_arc_union(path: Path = ARC_PATH, buffer_m: float = ARC_BUFFER_M):
    """Load ARC sidewalk layer, reproject to 32616, union + buffer."""
    if not path.exists():
        raise FileNotFoundError(
            f"ARC sidewalk cache not found: {path}. "
            f"See the module docstring for the refresh command."
        )
    arc = gpd.read_file(path)
    if arc.crs is None:
        arc = arc.set_crs(WGS84)
    arc = arc.to_crs(UTM16N)
    union = unary_union(arc.geometry.values)
    return union.buffer(buffer_m)


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return sidewalk_cov ∈ [0, 1] per segment_id.

    Algorithm:
      1. Reproject segments to EPSG:32616.
      2. arc_frac = (segment ∩ ARC_buffer_union).length / segment.length
      3. Combine ARC evidence with OSM as a belief prior — continuous
         output, no step functions:
         - OSM=yes     → 0.6 + 0.4 * arc_frac      → range [0.6, 1.0]
                         (positive prior; ARC raises confidence)
         - OSM=no      → 0.5 * arc_frac            → range [0.0, 0.5]
                         (negative prior; ARC can still drag toward 0)
         - OSM=unknown → arc_frac                  → range [0.0, 1.0]
                         (per TASKS line 23: missing tag ≠ 'no'; 0 only when
                         ARC also shows no coverage)
      4. Short-segment override (length < MIN_SCORE_LENGTH_M): use OSM
         signal alone, no ARC contribution. Prevents intersection-tail
         false positives where ARC linework crosses a sub-meter sliver.

    Output index matches `segments.index` (assumed to be `segment_id`).
    """
    if segments.empty:
        return pd.Series([], dtype=float, name="sidewalk_cov")

    arc_buf = _load_arc_union()

    segs_utm = segments.to_crs(UTM16N)
    seg_geoms = list(segs_utm.geometry.values)
    seg_lengths = segs_utm.geometry.length.values

    arc_frac = []
    for geom, L in zip(seg_geoms, seg_lengths):
        if L <= 0:
            arc_frac.append(0.0)
            continue
        inter = geom.intersection(arc_buf)
        frac = inter.length / L if not inter.is_empty else 0.0
        arc_frac.append(min(max(frac, 0.0), 1.0))

    osm_signals = [_osm_signal(row) for _, row in segments.iterrows()]

    out = []
    for L, sig, frac in zip(seg_lengths, osm_signals, arc_frac):
        if L < MIN_SCORE_LENGTH_M:
            # Short segment — arc_frac unreliable. OSM signal alone, no ARC bump.
            out.append(0.6 if sig == "yes" else 0.0)
            continue
        if sig == "yes":
            out.append(0.6 + 0.4 * frac)
        elif sig == "no":
            out.append(0.5 * frac)
        else:
            out.append(frac)

    return pd.Series(out, index=segments.index, dtype=float, name="sidewalk_cov")
