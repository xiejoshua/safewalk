#!/usr/bin/env python3
"""run_overlay.py — R4 data overlay integration script.

Reads scored_segments.parquet, runs all R4 factor modules in order,
writes enriched parquet back to the same file (or a new path via --out).

Usage:
    python -m scripts.run_overlay
    python -m scripts.run_overlay --in data/scored_segments.parquet --out data/enriched.parquet
    python -m scripts.run_overlay --dry-run   # print stats without writing
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

# layers/ is in backend/ — insert backend/ into sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from layers import canopy, crash, exposure, flooding, hazards, slope

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_overlay")

_DEFAULT_PARQUET = Path(__file__).resolve().parent.parent / "data" / "scored_segments.parquet"


def run(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Apply all R4 factor modules and return enriched GeoDataFrame."""
    out = segments.copy()

    # Scalar-returning modules: each produces a pd.Series indexed by segment_id
    scalar_modules: list[tuple[str, object]] = [
        ("crash_norm",    crash),
        ("hazard_norm",   hazards),
        ("canopy_pct",    canopy),
        ("exposure_norm", exposure),
        ("flooding",      flooding),
    ]

    for col, mod in scalar_modules:
        t0 = time.perf_counter()
        result: pd.Series = mod.score(segments)
        elapsed = time.perf_counter() - t0
        out[col] = result.reindex(out["segment_id"]).values
        if result.dtype == bool:
            logger.info("%-16s → True=%d / %d  (%.2fs)", col, int(result.sum()), len(result), elapsed)
        else:
            logger.info("%-16s → min=%.3f  max=%.3f  mean=%.3f  (%.2fs)",
                        col, result.min(), result.max(), result.mean(), elapsed)

    # Slope returns (slope_risk, barrier) tuple
    t0 = time.perf_counter()
    slope_risk, barrier = slope.score(segments)
    elapsed = time.perf_counter() - t0
    out["slope_risk"] = slope_risk.reindex(out["segment_id"]).values
    out["barrier"] = barrier.reindex(out["segment_id"]).values
    logger.info("%-16s → min=%.3f  max=%.3f  mean=%.3f  (%.2fs)",
                "slope_risk", slope_risk.min(), slope_risk.max(), slope_risk.mean(), elapsed)
    logger.info("%-16s → barrier_count=%d", "barrier", int(barrier.sum()))

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="R4 data overlay runner")
    parser.add_argument("--in", dest="input", default=str(_DEFAULT_PARQUET), help="Input parquet path")
    parser.add_argument("--out", dest="output", default=None, help="Output parquet path (default: overwrite input)")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing output")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path

    if not input_path.exists():
        logger.error("Input parquet not found: %s", input_path)
        logger.info("Run `python -m scripts.generate_stub_parquet` first to create stub data.")
        sys.exit(1)

    logger.info("Loading segments from %s", input_path)
    segments = gpd.read_parquet(input_path)
    logger.info("Loaded %d segments (CRS: %s)", len(segments), segments.crs)

    enriched = run(segments)

    r4_cols = ["crash_norm", "hazard_norm", "canopy_pct", "exposure_norm",
               "flooding", "slope_risk", "barrier"]
    logger.info("\nR4 column summary:")
    for col in r4_cols:
        if col in enriched.columns:
            series = enriched[col]
            if series.dtype == bool or series.dtype == object:
                logger.info("  %-16s  True=%d / %d", col, int(series.sum()), len(series))
            else:
                logger.info("  %-16s  min=%.3f  max=%.3f  mean=%.3f  null=%d",
                            col, series.min(), series.max(), series.mean(), series.isna().sum())

    if args.dry_run:
        logger.info("Dry run — not writing output.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(output_path)
    logger.info("Wrote enriched parquet to %s", output_path)


if __name__ == "__main__":
    main()
