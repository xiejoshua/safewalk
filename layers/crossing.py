"""
layers/crossing.py — emit crossing_penalty + crossing metadata per segment.

Reads OSM ``highway=crossing`` and ``highway=traffic_signals`` nodes from the
cached corpus and spatially snaps them onto walk-eligible segments. Emits:

  - ``crossing_penalty`` (float, [0.0, 0.225]): per-segment penalty matching
    R2's pre-audit formula verbatim — ``base × width × signalization``.
    Range capped at 0.225 (6+ lanes unsignalized). Backend passes through
    (audit Hard #3), applies ×2.5 for the ``accessible`` profile.
  - ``is_crossing`` (bool): True iff a ``highway=crossing`` node snapped to
    the segment.
  - ``crossing`` (string | None): OSM crossing type ("marked", "unmarked",
    "traffic_signals", "uncontrolled", ...) — for explanations.
  - ``traffic_signals`` (string | None): "yes" iff a signal node snapped to
    the segment OR the crossing node tags itself as signalized.
  - ``barrier`` (bool): True iff ``highway=steps`` OR ``wheelchair=no``.
    This is ``crossing.py``'s *partial* contribution; ``layers/slope.py``
    adds steep-grade barriers; ``prebake.py`` OR's them.

Two public functions:
  - ``score(segments)`` → ``pd.Series[float, name='crossing_penalty']``
    (canonical factor-module interface; sidewalk/traffic/crossing all share
    this shape).
  - ``enrich(segments)`` → ``pd.DataFrame`` with all five columns above.
    Used by ``prebake.py`` to join the metadata in one call.

No external data downloads — uses ``data/osm/<corridor>.json`` (already
cached during network build).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from layers.traffic import _parse_lanes_count, _str
from network.overpass import load_cached_osm, parse_node_tags

REPO = Path(__file__).resolve().parent.parent

WGS84 = "EPSG:4326"
UTM16N = "EPSG:32616"

# --- Penalty formula (matches R2's pre-audit `crossing_penalty()` exactly) ---
# Ported verbatim so backend's one-line passthrough (audit Hard #3) sees the
# numbers it was designed against. If we retune, change here only.
CROSSING_BASE_PENALTY = 0.15
LANES_PER_WIDTH_UNIT = 4.0   # 4 lanes = 1.0× multiplier
WIDTH_FACTOR_CAP = 1.5       # cap at 6+ lanes
SIGNALIZED_FACTOR = 0.4      # signalized crossings ~60% less dangerous
UNSIGNALIZED_FACTOR = 1.0

# Range of `crossing_penalty` for is_crossing segments:
#   2 lanes, signalized:    0.15 × 0.5 × 0.4 = 0.030
#   4 lanes, signalized:    0.15 × 1.0 × 0.4 = 0.060
#   4 lanes, unsignalized:  0.15 × 1.0 × 1.0 = 0.150
#   6+ lanes, unsignalized: 0.15 × 1.5 × 1.0 = 0.225 (max)


# --- Lane defaults when OSM `lanes` tag is missing -----------------------
# Class-based fallback. OSM `lanes` is sparse in Forest Park; defaulting to
# NaN would zero out the penalty math, so we estimate by road class.
CLASS_LANES_DEFAULT = {
    "footway": 0.0, "path": 0.0, "pedestrian": 0.0, "steps": 0.0,
    "service": 1.0, "living_street": 1.0,
    "residential": 2.0, "unclassified": 2.0,
    "tertiary": 2.0, "tertiary_link": 2.0,
    "secondary": 4.0, "secondary_link": 2.0,
    "primary": 4.0, "primary_link": 2.0,
}
CLASS_LANES_DEFAULT_FALLBACK = 2.0


# --- Spatial snap tolerance -----------------------------------------------
# Crossing/signal nodes sit on the way's path, not offset. 5 m is tight
# enough to avoid grabbing nodes from adjacent streets but loose enough to
# absorb minor coord-precision drift.
NODE_SNAP_M = 5.0


# --- Corridor name (used to load OSM cache) ------------------------------
# Read from corridor.json at runtime — matches the pattern used by
# scripts/build_sample_network.py.
def _corridor_name() -> str:
    import json
    return json.loads((REPO / "corridor.json").read_text())["name"]


# =========================================================================
# Internal helpers
# =========================================================================

def _load_crossing_nodes() -> gpd.GeoDataFrame:
    """Load OSM nodes tagged ``highway in {crossing, traffic_signals}`` from
    the cached corpus. Returns a WGS84 GeoDataFrame with columns
    ``(node_id, highway, crossing, crossing_signals, geometry)``.
    """
    name = _corridor_name()
    data = load_cached_osm(name)

    # We need (lon, lat) for every node id and tags for nodes that have them.
    # parse_elements returns all node coords; parse_node_tags returns just
    # the tagged subset. Intersect them.
    from network.overpass import parse_elements  # local import to avoid cycle
    coords, _ = parse_elements(data)
    tags = parse_node_tags(data)

    rows = []
    for nid, tag_dict in tags.items():
        hw = _str(tag_dict.get("highway"))
        if hw not in {"crossing", "traffic_signals"}:
            continue
        lon, lat = coords[nid]
        rows.append({
            "node_id": int(nid),
            "highway": hw,
            "crossing": tag_dict.get("crossing"),
            "crossing_signals": tag_dict.get("crossing:signals"),
            "geometry": Point(lon, lat),
        })

    if not rows:
        return gpd.GeoDataFrame(
            columns=["node_id", "highway", "crossing", "crossing_signals", "geometry"],
            geometry="geometry", crs=WGS84,
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)


def _is_signalized(crossing_tag, crossing_signals_tag, has_signal_node: bool) -> bool:
    """A crossing is signalized if any signal evidence is present."""
    if has_signal_node:
        return True
    if _str(crossing_tag) == "traffic_signals":
        return True
    if _str(crossing_signals_tag) == "yes":
        return True
    return False


def _snap_nodes_to_segments(
    segments: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    snap_m: float = NODE_SNAP_M,
) -> pd.DataFrame:
    """Find ALL segments within ``snap_m`` of each node in EPSG:32616 and
    aggregate per segment_id.

    Uses an intersects-with-buffer pattern rather than `sjoin_nearest` because
    at intersections, a single node (especially `highway=traffic_signals`)
    sits at the meeting point of multiple ways — and the signal genuinely
    applies to all of them. `sjoin_nearest` would arbitrarily pick one.

    Returns a DataFrame indexed by ``segment_id`` with columns:
      - ``is_crossing`` (bool): True if any `highway=crossing` is in buffer
      - ``crossing`` (str | None): first non-null crossing tag value seen
      - ``has_signal_node`` (bool): True if a `highway=traffic_signals` is in buffer
      - ``crossing_signals`` (str | None): from a near crossing node
    Segments with no node within range are absent from the output (caller
    fills with defaults).
    """
    if segments.empty or nodes.empty:
        return pd.DataFrame(columns=["is_crossing", "crossing", "has_signal_node", "crossing_signals"])

    segs_utm = segments[["geometry"]].to_crs(UTM16N)
    nodes_utm = nodes.to_crs(UTM16N).copy()
    # Buffer each node into a small disk; sjoin with intersects catches every
    # segment that touches that disk (a tie-at-zero hits all of them).
    nodes_utm["geometry"] = nodes_utm.geometry.buffer(snap_m)

    joined = gpd.sjoin(nodes_utm, segs_utm, predicate="intersects", how="left")
    joined = joined.dropna(subset=["segment_id"])
    if joined.empty:
        return pd.DataFrame(columns=["is_crossing", "crossing", "has_signal_node", "crossing_signals"])

    # Aggregate per segment_id: a single segment might be touched by multiple
    # nodes (one crossing + one signal at the same intersection, for instance).
    joined["is_crossing_node"] = joined["highway"] == "crossing"
    joined["is_signal_node"] = joined["highway"] == "traffic_signals"
    grouped = joined.groupby("segment_id").agg(
        is_crossing=("is_crossing_node", "any"),
        has_signal_node=("is_signal_node", "any"),
        # First non-null crossing type / signal tag from any snapped node:
        crossing=("crossing", lambda s: next((v for v in s if pd.notna(v)), None)),
        crossing_signals=("crossing_signals", lambda s: next((v for v in s if pd.notna(v)), None)),
    )
    return grouped


def _lanes_per_segment(segments: gpd.GeoDataFrame) -> pd.Series:
    """Parse OSM `lanes` to float; fill missing with class default."""
    parsed = segments["lanes"].apply(_parse_lanes_count)
    class_default = segments["highway"].apply(
        lambda hw: CLASS_LANES_DEFAULT.get(_str(hw), CLASS_LANES_DEFAULT_FALLBACK)
    )
    out = parsed.where(parsed.notna(), class_default)
    return out.astype(float)


def _compute_penalty(is_crossing: bool, lanes: float, signalized: bool) -> float:
    """Per-segment crossing penalty. 0.0 if no crossing on segment."""
    if not is_crossing:
        return 0.0
    if lanes <= 0:
        return 0.0
    width = min(lanes / LANES_PER_WIDTH_UNIT, WIDTH_FACTOR_CAP)
    sig_factor = SIGNALIZED_FACTOR if signalized else UNSIGNALIZED_FACTOR
    return CROSSING_BASE_PENALTY * width * sig_factor


# =========================================================================
# Public API
# =========================================================================

def enrich(segments: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return all five crossing columns indexed by ``segments.index``.

    Columns:
      - ``crossing_penalty`` (float64, [0.0, 0.225])
      - ``is_crossing`` (bool)
      - ``crossing`` (object — string or None)
      - ``traffic_signals`` (object — "yes" or None)
      - ``barrier`` (bool) — ``crossing.py``'s contribution
        (steps / wheelchair=no only; slope.py adds grade > 10% later)

    No NaN in any column. Safe to column-join onto the segment parquet.
    """
    n = len(segments)
    empty = pd.DataFrame({
        "crossing_penalty": pd.Series([0.0] * n, index=segments.index, dtype=float),
        "is_crossing": pd.Series([False] * n, index=segments.index, dtype=bool),
        "crossing": pd.Series([None] * n, index=segments.index, dtype=object),
        "traffic_signals": pd.Series([None] * n, index=segments.index, dtype=object),
        "barrier": pd.Series([False] * n, index=segments.index, dtype=bool),
    })

    # Barrier: crossing.py's contribution = steps OR wheelchair=no.
    highways = segments["highway"].apply(_str)
    wheelchairs = segments["wheelchair"].apply(_str)
    barrier = (highways == "steps") | (wheelchairs == "no")
    empty["barrier"] = barrier.values

    if segments.empty:
        return empty

    nodes = _load_crossing_nodes()
    if nodes.empty:
        return empty

    snapped = _snap_nodes_to_segments(segments, nodes)
    if snapped.empty:
        return empty

    # Align snapped → full segments index (missing ones get default values).
    lanes = _lanes_per_segment(segments)

    out = empty.copy()
    matched_index = snapped.index.intersection(segments.index)

    for seg_id in matched_index:
        row = snapped.loc[seg_id]
        is_xing = bool(row["is_crossing"])
        has_sig = bool(row["has_signal_node"])
        xing_type = row["crossing"]
        xing_signals = row["crossing_signals"]
        signalized = _is_signalized(xing_type, xing_signals, has_sig)

        out.at[seg_id, "is_crossing"] = is_xing
        out.at[seg_id, "crossing"] = xing_type
        out.at[seg_id, "traffic_signals"] = "yes" if (has_sig or signalized) else None

        penalty = _compute_penalty(is_xing, lanes.at[seg_id], signalized)
        out.at[seg_id, "crossing_penalty"] = penalty

    return out


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Canonical factor-module interface — returns the ``crossing_penalty``
    Series only. Same shape as ``sidewalk.score`` and ``traffic.score``.

    For the full metadata schema (is_crossing, crossing, traffic_signals,
    barrier), call :func:`enrich`.
    """
    df = enrich(segments)
    s = df["crossing_penalty"].astype(float)
    s.name = "crossing_penalty"
    return s
