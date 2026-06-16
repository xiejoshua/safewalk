# Quickstart Validation: R4 Data — Hazards, Environment & Supabase

**Purpose**: Prove each R4 deliverable works end-to-end before the Day 1 PM milestone.

---

## Prerequisites

1. Python 3.12 venv active: `cd backend && source .venv/bin/activate` (or `.venv\Scripts\activate` on Windows)
2. New dependencies installed: `pip install rasterio rasterstats supabase`
3. R3 sample network available at `backend/data/scored_segments.parquet` (or use the stub)
4. Raster files in `backend/data/`: `meta_canopy.tif`, `capa_heat.tif`, `dem.tif`
   (skip individual layer tests for any raster not yet downloaded)
5. `backend/data/Crashes_2020-2024.geojson` present (already in repo ✅)

---

## Validate 1 — Crash module produces real values

```bash
cd backend
python - <<'EOF'
import geopandas as gpd
from layers.crash import score

segs = gpd.read_parquet("data/scored_segments.parquet")[["segment_id","geometry"]]
result = score(segs)
assert result.between(0.0, 1.0).all(), "Values out of [0,1]"
assert result.notna().all(), "NaN values not filled"
print(f"crash_norm — min={result.min():.3f}  max={result.max():.3f}  mean={result.mean():.3f}")
EOF
```

**Expected**: Prints a line with non-zero max (some segments near crashes).
At least one segment should have `crash_norm > 0` given the Gillem corridor.

---

## Validate 2 — Hazards module (with local Open311 cache, no live request)

```bash
python - <<'EOF'
import geopandas as gpd
from layers.hazards import score

segs = gpd.read_parquet("data/scored_segments.parquet")[["segment_id","geometry"]]
result = score(segs)
assert result.between(0.0, 1.0).all()
print(f"hazard_norm — min={result.min():.3f}  max={result.max():.3f}")
# Segments with no hazard within 20 m should be 0.0 (most will be)
print(f"Segments with hazard_norm=0: {(result == 0.0).sum()} / {len(result)}")
EOF
```

**Expected**: Most segments score 0.0 (no hazard nearby). Any segment within 20 m
of an Open311 or gap report gets a penalty > 0.

---

## Validate 3 — Canopy module

```bash
python - <<'EOF'
import geopandas as gpd
from layers.canopy import score

segs = gpd.read_parquet("data/scored_segments.parquet")[["segment_id","geometry"]]
result = score(segs)
assert result.between(0.0, 1.0).all()
print(f"canopy_pct — min={result.min():.3f}  max={result.max():.3f}")
EOF
```

**Expected**: Values in [0, 1]; segments under tree cover score > 0.

---

## Validate 4 — Full overlay integration (all R4 columns together)

```bash
python - <<'EOF'
import geopandas as gpd
from layers import crash, hazards, canopy, exposure, slope

segs = gpd.read_parquet("data/scored_segments.parquet")
segs["crash_norm"]    = crash.score(segs).reindex(segs["segment_id"]).fillna(0.0).values
segs["hazard_norm"]   = hazards.score(segs).reindex(segs["segment_id"]).fillna(0.0).values
segs["canopy_pct"]    = canopy.score(segs).reindex(segs["segment_id"]).fillna(0.0).values
segs["exposure_norm"] = exposure.score(segs).reindex(segs["segment_id"]).fillna(segs.get("exposure_norm", 0.5)).values
sr, bar = slope.score(segs)
segs["slope_risk"]    = sr.reindex(segs["segment_id"]).fillna(0.0).values
segs["barrier"]       = bar.reindex(segs["segment_id"]).fillna(False).values

r4_cols = ["crash_norm","hazard_norm","canopy_pct","exposure_norm","slope_risk","barrier"]
print(segs[r4_cols].describe())

# Write the enriched parquet back (for R2 to test scoring)
segs.to_parquet("data/scored_segments.parquet")
print("Wrote enriched parquet ✅")
EOF
```

**Expected**: All six R4 columns present; no NaN; `barrier` is boolean.
The scoring engine (`uvicorn app.main:app --reload`) should now return real
(non-stub) risk scores when the enriched parquet is loaded.

---

## Validate 5 — Supabase gap_reports: insert + realtime

1. Apply the schema in Supabase SQL editor:
   ```bash
   # Paste contents of backend/supabase/schema.sql into Supabase → SQL editor → Run
   # Then paste backend/supabase/seed.sql
   ```

2. Set env vars in `backend/.env`:
   ```
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_ANON_KEY=<anon key>
   ```

3. Test insert:
   ```python
   from supabase import create_client
   import os
   sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
   r = sb.table("gap_reports").insert({
       "geom": "SRID=4326;POINT(-84.3921 33.6958)",
       "type": "no_sidewalk",
       "note": "Test report"
   }).execute()
   print(r)
   ```

4. In a second terminal / browser, open the frontend dashboard and verify the
   pin appears within 5 seconds without a page refresh.

**Expected**: Insert returns no error; pin appears live on the map.

---

## Validate 6 — Scoring engine uses real R4 data

```bash
# With enriched parquet in place and MAPBOX_ACCESS_TOKEN set:
uvicorn app.main:app --reload --port 8000

curl -s -X POST http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d '{"origin":[-84.40,33.69],"dest":[-84.35,33.71],"profile":"day"}' \
  | python -m json.tool | head -30
```

**Expected**: Response contains `safest.score` that differs from the stub (no longer
all mid-range random values). The `explanation` field references real safety factors.
