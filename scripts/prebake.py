"""
prebake.py — R3 orchestrator. Builds the full corridor's scored_segments.parquet.

Pipeline:
  1. Build the walk network from cached OSM (`data/osm/<corridor>.json`)
  2. Run all R3 factor modules    (sidewalk, traffic, crossing)
  3. Run all R4 factor modules    (canopy, crash, exposure, flooding, hazards, slope)
     Each wrapped in try/except — failure fills the column with the documented
     default (0.0) and surfaces a warning in the sidecar.
  4. OR-merge the `barrier` contributions from crossing.py and slope.py
  5. Coerce raw OSM `lanes` / `maxspeed` / `width` to numeric (R2 Hard #1)
  6. Strict NaN assertion on all factor columns — fail loud before write
  7. Build sidecar JSON with per-column stats + canary warnings
  8. Atomic write: tmpfile → os.replace(final_path)

Output (tracked, pushable — backend/data/ is gitignored):
  outputs/scored_segments.parquet     — scored network for the backend to read
  outputs/scored_segments.meta.json   — receipts + canary warnings

This is the canonical pipeline. `backend/scripts/run_overlay.py` is left
in place for now but is functionally superseded.

CLI:
  python scripts/prebake.py
  python scripts/prebake.py --bbox -84.37 33.58 -84.29 33.65
  python scripts/prebake.py --skip crash_norm --skip hazard_norm    # offline iteration
  python scripts/prebake.py --no-r4                                  # R3 only
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from network.build import (  # noqa: E402
    explode_tags,
    filter_walk_eligible,
    segmentize_edges,
    ways_to_gdf,
)
from network.overpass import load_cached_osm, parse_elements  # noqa: E402

from layers import sidewalk, traffic, crossing  # noqa: E402
from layers import canopy, crash, exposure, flooding, hazards, slope  # noqa: E402
from layers.traffic import _parse_lanes_count, _parse_maxspeed_mph  # noqa: E402

CORRIDOR_PATH = REPO / "corridor.json"
OUT_PARQUET = REPO / "outputs" / "scored_segments.parquet"
OUT_SIDECAR = REPO / "outputs" / "scored_segments.meta.json"

FACTOR_COLUMNS: list[str] = [
    "sidewalk_cov",
    "traffic_risk",
    "crash_norm",
    "hazard_norm",
    "canopy_pct",
    "exposure_norm",
    "slope_risk",
    "crossing_penalty",
    "flooding",
]

# Columns that are legitimately uniform across the corridor — do not fire the
# "1 distinct value" canary for these.
#   flooding:      all-0.0 is valid when no segment intersects an SFHA polygon.
#   exposure_norm: ERA5's ~25 km grid is coarser than the ~5 km corridor, so the
#                  base reading is uniform by design (see exposure.py docstring).
#                  apply_exposure_shade() reintroduces variation when canopy is
#                  available, but uniformity alone is not a failure signal.
EXPECTED_UNIFORM: set[str] = {"flooding", "exposure_norm"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("prebake")


# =========================================================================
# Network build
# =========================================================================

def build_full_network(corridor_name: str) -> gpd.GeoDataFrame:
    """Same recipe as scripts/build_sample_network.py minus the slice."""
    log.info("loading OSM cache: %s", corridor_name)
    data = load_cached_osm(corridor_name)
    nodes, ways = parse_elements(data)
    log.info("parsed nodes=%d  ways=%d", len(nodes), len(ways))

    gdf = ways_to_gdf(ways, nodes)
    gdf = filter_walk_eligible(gdf)
    log.info("walk-eligible ways: %d", len(gdf))

    seg = segmentize_edges(gdf, target_m=25.0, min_tail_m=3.0)
    seg = explode_tags(seg)
    log.info("segmentized: %d segments / %.2f km", len(seg), seg.length_m.sum() / 1000)
    return seg


# =========================================================================
# R3 factors — contract: no NaN, no exceptions
# =========================================================================

def run_r3_factors(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    t0 = time.perf_counter()
    segments["sidewalk_cov"] = sidewalk.score(segments)
    log.info("sidewalk_cov   in %.2fs  (mean=%.3f)", time.perf_counter() - t0, segments["sidewalk_cov"].mean())

    t0 = time.perf_counter()
    segments["traffic_risk"] = traffic.score(segments)
    log.info("traffic_risk   in %.2fs  (mean=%.3f)", time.perf_counter() - t0, segments["traffic_risk"].mean())

    t0 = time.perf_counter()
    xing = crossing.enrich(segments)
    segments["crossing_penalty"] = xing["crossing_penalty"]
    segments["is_crossing"]      = xing["is_crossing"]
    segments["crossing"]         = xing["crossing"]
    segments["traffic_signals"]  = xing["traffic_signals"]
    segments["_barrier_crossing"] = xing["barrier"]
    log.info("crossing       in %.2fs  (mean=%.3f, is_crossing=%d)",
             time.perf_counter() - t0, segments["crossing_penalty"].mean(),
             int(segments["is_crossing"].sum()))

    return segments


# =========================================================================
# R4 factors — wrapped in try/except; failures recorded as warnings
# =========================================================================

def _safe_score(name: str, fn, segments: gpd.GeoDataFrame, default_value: float = 0.0):
    """Call a factor module; on any exception, return (zeros_series, warning_str)."""
    try:
        t0 = time.perf_counter()
        result = fn(segments)
        log.info("%-14s in %.2fs  (mean=%.3f)", name, time.perf_counter() - t0,
                 float(result.mean()) if result.dtype.kind in "fi" else float("nan"))
        return result, None
    except Exception as exc:
        warning = f"{name} failed: {exc.__class__.__name__}: {exc}"
        log.warning("%s; filling with %s", warning, default_value)
        return (
            pd.Series(default_value, index=segments.index, dtype=float),
            warning,
        )


def run_r4_factors(segments: gpd.GeoDataFrame, skip: set[str], no_r4: bool):
    """Run R4 modules. Returns (segments_with_columns, list_of_warnings)."""
    warnings: list[str] = []

    scalar_mods = [
        ("crash_norm",    crash.score),
        ("hazard_norm",   hazards.score),
        ("canopy_pct",    canopy.score),
        ("exposure_norm", exposure.score),
        ("flooding",      flooding.score),
    ]

    for col, fn in scalar_mods:
        if no_r4 or col in skip:
            segments[col] = 0.0
            warnings.append(f"{col} skipped (no_r4={no_r4}, --skip)")
            continue
        result, w = _safe_score(col, fn, segments)
        segments[col] = result.reindex(segments.index).fillna(0.0).astype(float).values
        if w:
            warnings.append(w)

    # Slope returns a tuple — special case
    if no_r4 or "slope_risk" in skip:
        segments["slope_risk"] = 0.0
        segments["_barrier_slope"] = False
        warnings.append("slope skipped (no_r4 or --skip)")
    else:
        try:
            t0 = time.perf_counter()
            sr, sb = slope.score(segments)
            log.info("slope          in %.2fs  (mean_risk=%.3f, barrier_count=%d)",
                     time.perf_counter() - t0, float(sr.mean()), int(sb.sum()))
            segments["slope_risk"] = sr.reindex(segments.index).fillna(0.0).astype(float).values
            segments["_barrier_slope"] = sb.reindex(segments.index).fillna(False).astype(bool).values
        except Exception as exc:
            w = f"slope failed: {exc.__class__.__name__}: {exc}"
            log.warning("%s; slope_risk=0, barrier=False", w)
            warnings.append(w)
            segments["slope_risk"] = 0.0
            segments["_barrier_slope"] = False

    return segments, warnings


# =========================================================================
# Post-processing
# =========================================================================

def apply_exposure_shade(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Modulate uniform heat exposure by local tree shade: exposure × (1 − canopy).

    ERA5's ~25 km grid makes exposure_norm uniform across the corridor; canopy_pct
    reintroduces spatial variation (shaded segments feel less heat). Only applied
    when canopy actually varies, so a failed/zeroed canopy run never wipes exposure.
    """
    if "exposure_norm" not in segments.columns or "canopy_pct" not in segments.columns:
        return segments
    if segments["canopy_pct"].nunique() <= 1:
        log.info("exposure: canopy uniform/unavailable — leaving exposure_norm unmodulated")
        return segments
    segments["exposure_norm"] = (
        (1.0 - segments["canopy_pct"]) * segments["exposure_norm"]
    ).clip(0.0, 1.0)
    log.info("exposure: modulated by canopy shade (mean=%.3f)", segments["exposure_norm"].mean())
    return segments


def merge_barriers(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Single `barrier` column = crossing-side OR slope-side. Drops intermediates."""
    bc = segments["_barrier_crossing"].fillna(False).astype(bool)
    bs = segments["_barrier_slope"].fillna(False).astype(bool)
    segments["barrier"] = (bc | bs)
    return segments.drop(columns=["_barrier_crossing", "_barrier_slope"])


def coerce_numeric(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Convert OSM string columns to numeric per R2 Hard #1."""
    for col, parser in [
        ("lanes", _parse_lanes_count),
        ("width", _parse_lanes_count),
        ("maxspeed", _parse_maxspeed_mph),
    ]:
        if col in segments.columns:
            segments[col] = segments[col].apply(parser).astype(float)
    return segments


def assert_no_nan(segments: gpd.GeoDataFrame, factor_cols: list[str]) -> None:
    """Strict policy: any NaN in a factor column = bug. Fail loud."""
    for col in factor_cols:
        nan_mask = segments[col].isna()
        if nan_mask.any():
            bad = segments[nan_mask].index.tolist()[:5]
            raise ValueError(
                f"{col} contains {int(nan_mask.sum())} NaN values. "
                f"First 5 segment_ids: {bad}. "
                f"Modules must emit a clean default; prebake will not fill silently."
            )
    # barrier is bool — safe to coerce NaN → False
    if "barrier" in segments.columns:
        segments["barrier"] = segments["barrier"].fillna(False).astype(bool)


# =========================================================================
# Sidecar
# =========================================================================

def _column_stats(s: pd.Series) -> dict:
    stats: dict = {"count": int(len(s)), "nan": int(s.isna().sum()), "distinct": int(s.nunique())}
    if s.dtype.kind in "fi":
        non_null = s.dropna()
        if len(non_null):
            stats.update({
                "mean":   float(non_null.mean()),
                "min":    float(non_null.min()),
                "max":    float(non_null.max()),
                "stddev": float(non_null.std()) if len(non_null) > 1 else 0.0,
            })
    return stats


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def build_sidecar(
    segments: gpd.GeoDataFrame,
    corridor: dict,
    bbox: list[float],
    r4_warnings: list[str],
) -> dict:
    canary: list[str] = []
    columns_report: dict[str, dict] = {}

    for col in FACTOR_COLUMNS + ["barrier"]:
        if col not in segments.columns:
            canary.append(f"{col} missing from output schema")
            continue
        stats = _column_stats(segments[col])
        columns_report[col] = stats
        if stats.get("distinct") == 1 and col not in EXPECTED_UNIFORM:
            mean_v = stats.get("mean")
            canary.append(
                f"{col} has only 1 distinct value (mean={mean_v}); module may have silently failed."
            )

    osm_cache_path = REPO / "data" / "osm" / f"{corridor['name']}.json"
    return {
        "corridor_name":     corridor["name"],
        "bbox":              bbox,
        "row_count":         int(len(segments)),
        "crs":               "EPSG:4326",
        "generated_at":      dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_head":          _git_head(),
        "osm_cache_sha256":  _sha256_of(osm_cache_path) if osm_cache_path.exists() else None,
        "columns":           columns_report,
        "r4_module_failures": r4_warnings,
        "canary_warnings":   canary,
        "head_segment_ids":  sorted(segments.index.tolist())[:5],
    }


# =========================================================================
# Atomic write
# =========================================================================

def _atomic_write(target: Path, write_fn) -> None:
    """Write to a tmpfile in target's dir, then os.replace into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False, dir=str(target.parent), suffix=target.suffix
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        write_fn(tmp_path)
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def write_parquet(segments: gpd.GeoDataFrame, target: Path) -> None:
    _atomic_write(target, lambda p: segments.to_parquet(p))


def write_sidecar(sidecar: dict, target: Path) -> None:
    _atomic_write(target, lambda p: p.write_text(json.dumps(sidecar, indent=2) + "\n"))


# =========================================================================
# CLI
# =========================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build scored_segments.parquet for the locked corridor."
    )
    p.add_argument("--corridor", default=None, help="corridor.json `name` override")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                   default=None, help="bbox override (else from corridor.json)")
    p.add_argument("--out", default=str(OUT_PARQUET),
                   help=f"output parquet path (default: {OUT_PARQUET})")
    p.add_argument("--skip", action="append", default=[],
                   help="skip a specific factor column (repeatable)")
    p.add_argument("--no-r4", action="store_true",
                   help="skip all R4 modules (offline R3 iteration)")
    p.add_argument("--quiet", action="store_true",
                   help="reduce per-module log output")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    corridor = json.loads(CORRIDOR_PATH.read_text())
    if args.corridor:
        corridor["name"] = args.corridor
    bbox = args.bbox if args.bbox is not None else corridor["bbox"]

    out_parquet = Path(args.out)
    out_sidecar = out_parquet.with_suffix(".meta.json")

    log.info("=== prebake start: corridor=%s bbox=%s ===", corridor["name"], bbox)

    segments = build_full_network(corridor["name"])
    segments = run_r3_factors(segments)
    segments, r4_warnings = run_r4_factors(segments, skip=set(args.skip), no_r4=args.no_r4)
    segments = apply_exposure_shade(segments)
    segments = merge_barriers(segments)
    segments = coerce_numeric(segments)
    assert_no_nan(segments, FACTOR_COLUMNS)

    sidecar = build_sidecar(segments, corridor, bbox, r4_warnings)

    write_parquet(segments, out_parquet)
    write_sidecar(sidecar, out_sidecar)

    log.info("=== prebake done ===")
    log.info("wrote %s (%d rows)", out_parquet, len(segments))
    log.info("wrote %s", out_sidecar)
    if sidecar["canary_warnings"]:
        log.warning("CANARY WARNINGS (%d):", len(sidecar["canary_warnings"]))
        for w in sidecar["canary_warnings"]:
            log.warning("  %s", w)
    if r4_warnings:
        log.warning("R4 module failures (%d):", len(r4_warnings))
        for w in r4_warnings:
            log.warning("  %s", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
