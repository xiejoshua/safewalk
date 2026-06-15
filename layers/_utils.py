"""Shared utilities for R4 factor modules."""
from __future__ import annotations

import pandas as pd

# KABCO severity weights: K=Fatal, A=Suspected Serious, B=Suspected Minor,
# C=Possible Injury, O=Property Damage Only
_KABCO_WEIGHTS: dict[str, float] = {
    "K": 5.0,  # fatal
    "A": 4.0,  # suspected serious injury
    "B": 2.0,  # suspected minor injury
    "C": 1.0,  # possible injury
    "O": 0.5,  # property damage only
}


def normalize(series: pd.Series) -> pd.Series:
    """Min-max normalize to [0, 1]. Returns zeros if all values are equal."""
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.0, index=series.index, dtype=float)
    return ((series - lo) / (hi - lo)).clip(0.0, 1.0)


def weight_by_kabco(severity: str) -> float:
    """Map a KABCO severity code to a numeric crash weight."""
    if not severity:
        return _KABCO_WEIGHTS["O"]
    # The field may be formatted as "(K) Fatal" or just "K"
    for code, weight in _KABCO_WEIGHTS.items():
        if code in str(severity).upper():
            return weight
    return _KABCO_WEIGHTS["O"]
