# Contract: Factor Module Interface (R4 ↔ R3)

**Parties**: R4 (implementer of crash/hazards/canopy/exposure/slope modules) ↔
R3 (owner of `prebake.py` orchestrator)

**Locked at kickoff**: 2026-06-14

---

## Interface

Every R4 factor module in `backend/layers/<factor>.py` MUST export exactly one
public function named `score`:

```python
import geopandas as gpd
import pandas as pd

def score(segments: gpd.GeoDataFrame) -> pd.Series:
    ...
```

### Input contract

| Parameter | Type | Guaranteed columns | CRS |
|---|---|---|---|
| `segments` | `gpd.GeoDataFrame` | `segment_id` (str), `geometry` (LineString) | EPSG:4326 (WGS84) |

R3 may pass additional OSM columns (e.g., `highway`, `maxspeed`, `sidewalk`,
`wheelchair`, `lanes`). Factor modules MAY read these; they MUST NOT fail if
a column is absent (use `.get()` or `pd.Series.fillna()`).

The input GeoDataFrame MUST NOT be modified in place.

### Output contract

| Property | Requirement |
|---|---|
| Type | `pd.Series` |
| Index | `segment_id` values matching the input (same dtype: str) |
| Values | `float` in `[0.0, 1.0]` |
| Missing segments | Return `NaN` for segments the module cannot score. The orchestrator fills NaN with the documented default for that column. |
| Infinite values | NOT allowed. Hard barriers are signaled via the `barrier` column (from `slope.py`), not via `inf` in the score Series. |

### Example skeleton

```python
# backend/layers/crash.py
from __future__ import annotations
import geopandas as gpd
import pandas as pd

# Null policy: segments with no crashes within 30 m buffer → 0.0
NULL_DEFAULT = 0.0

def score(segments: gpd.GeoDataFrame) -> pd.Series:
    segs_m = segments.to_crs(32616)
    # ... compute crash_norm ...
    result = pd.Series(crash_norm_values, index=segments["segment_id"], dtype=float)
    return result.clip(0.0, 1.0)
```

### Additional outputs (slope module only)

`backend/layers/slope.py` returns TWO Series via a tuple:

```python
def score(segments: gpd.GeoDataFrame) -> tuple[pd.Series, pd.Series]:
    """Returns (slope_risk_series, barrier_series)."""
    ...
```

The orchestrator unpacks this and assigns both columns separately.

---

## Orchestrator call pattern (R3's prebake.py)

```python
from layers import crash, hazards, canopy, exposure, slope

segments["crash_norm"]   = crash.score(segments).reindex(segments["segment_id"]).fillna(0.0)
segments["hazard_norm"]  = hazards.score(segments).reindex(segments["segment_id"]).fillna(0.0)
segments["canopy_pct"]   = canopy.score(segments).reindex(segments["segment_id"]).fillna(0.0)
segments["exposure_norm"]= exposure.score(segments).reindex(segments["segment_id"]).fillna(segments["exposure_norm"].mean())
slope_risk, barrier      = slope.score(segments)
segments["slope_risk"]   = slope_risk.reindex(segments["segment_id"]).fillna(0.0)
segments["barrier"]      = barrier.reindex(segments["segment_id"]).fillna(False)
```

---

## Environment variables required by hazards.py

| Variable | Purpose | Required |
|---|---|---|
| `SUPABASE_URL` | Supabase project URL for reading gap_reports | Optional — module skips gap_reports if unset |
| `SUPABASE_ANON_KEY` | Supabase anon key | Optional — same condition |

If Supabase env vars are absent, `hazards.py` uses only Open311 data (or a local
cache file). This ensures the module works offline during bake.
