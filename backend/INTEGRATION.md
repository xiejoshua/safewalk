# R2 ↔ R3 integration — action list

**Audience:** R2 (backend, `backend/` directory on this branch).
**Author:** R3 (data pipeline, branch `r3/sample-network`).
**Date:** 2026-06-15.

This is the audit of `backend/` against the R3 sample network
(`data/sample_network.parquet` on `r3/sample-network`, 226 segments, 5.26 km
around the Gillem entrance). Goal: line up the contracts before R3 wires
factor columns and R2 swaps the stub for real data.

---

## TL;DR (3 bullets)

1. **Five places in `backend/app/` will crash or silently mis-score on real
   R3 data.** All are listed below with file:line and a fix.
2. **Your stub bbox is in the wrong corridor.** `generate_stub_parquet.py`
   covers `(-84.42, 33.68, -84.33, 33.72)`; `corridor.json` locks Gillem
   at `[-84.37, 33.58, -84.29, 33.65]`. These don't overlap.
3. **Five questions at the bottom** — answer those and R3 unblocks. Most
   are pick-one decisions, not open design.

---

## The R3 schema (what real data will look like)

The sample parquet ships these columns. Real `scored_segments.parquet`
adds the factor columns on top; everything else is identical.

| Column | Type | Notes |
|---|---|---|
| `segment_id` | string, **index + column** | `f"{osm_way_id}-{idx:04d}"`, e.g. `"561606557-0000"` |
| `osm_way_id` | int64 | parent OSM way |
| `segment_index` | int64 | 0..N along the way |
| `geometry` | LineString, **EPSG:4326** | canonical store/serve CRS |
| `length_m` | float64 | computed in EPSG:32616 |
| `highway` | string | OSM class (`residential`, `tertiary`, `service`, …) |
| `sidewalk`, `sidewalk:left`, `sidewalk:right` | string \| None | raw OSM |
| `footway`, `foot`, `access`, `wheelchair`, `kerb`, `surface`, `service`, `name` | string \| None | raw OSM |
| `maxspeed`, `lanes`, `width` | **string** \| None | **raw OSM — see Hard #1** |

**Added by the factor pipeline** (TASKS.md line 5):

| Column | Type | Range |
|---|---|---|
| `sidewalk_cov` | float | [0, 1] |
| `traffic_risk` | float | [0, 1] |
| `crash_norm` | float | [0, 1] |
| `hazard_norm` | float | [0, 1] |
| `canopy_pct` | float | [0, 1] |
| `exposure_norm` | float | [0, 1] |
| `slope_risk` | float | [0, 1] — **clipped, never >1** |
| `barrier` | bool | true = steps, wheelchair=no, or grade>10% |
| `crossing_penalty` | float | per TASKS.md line 37 — pending decision (see Hard #3) |

CRS rule: `EPSG:4326` for store/serve; `EPSG:32616` (UTM 16N) for any
buffer / distance math. `length_m` is cached so nobody recomputes in
WGS84.

---

## Hard conflicts (will crash or mis-score on real data)

### Hard #1 — `lanes` / `maxspeed` / `width` dtype mismatch
**Files:** `backend/app/scoring.py:107`, `backend/scripts/generate_stub_parquet.py:58`, `backend/app/segments.py:119`.

OSM stores these as strings: `"2"`, `"2;3"`, `"variable"`, `"35 mph"`.
Three different dtypes are currently expected for `lanes` alone:
- Stub: `int` (`rng.choice([2, 4, 6])`)
- Empty store: `dtype=float`
- `scoring.py:107`: `float(seg.get("lanes") or 2)` — will `ValueError`
  on `"2;3"`

**Fix — R3 will coerce** to numeric in the factor pipeline (taking the
max of split values, NaN-safe). After that the parquet column will be
`float64 | NaN`. Confirm and:
- Change `_grid_segments` stub to emit floats.
- Change `create_empty_store` to keep `lanes: float` (already does).
- `scoring.py:107` can stay as-is once strings are gone.

### Hard #2 — node-tag columns missing on segments
**Files:** `backend/app/scoring.py:103, 109`, `backend/app/segments.py:25–28`.

Your scorer reads `is_crossing`, `traffic_signals`, `crossing` directly
off each segment row:

```python
# scoring.py:103
if not seg.get("is_crossing"):
    return 0.0
# scoring.py:109
signalized = seg.get("traffic_signals") or seg.get("crossing") == "traffic_signals"
```

These are OSM **node** tags, not way tags. The sample parquet has
none of them. TASKS.md line 16 says crossings live in a separate
`crossings.parquet` keyed by node id.

**Result on real data:** `is_crossing` is always None → `crossing_penalty()`
returns `0.0` for every segment → night-mode scoring loses the crossing
signal entirely.

**Fix — pick one of:**
- (a) R3 joins node tags back to incident segments and emits
  `is_crossing` / `traffic_signals` / `crossing` columns on segments.
  Backend code unchanged. *Recommended* — keeps backend out of the
  spatial-join business.
- (b) Backend loads `crossings.parquet` separately at startup and
  joins at snap-time via segment_id.

### Hard #3 — `crossing_penalty` ownership is double-counted
**Files:** `TASKS.md:37`, `backend/app/scoring.py:101–115`.

TASKS.md line 37 says:

> Output a `crossing_penalty` column per segment so the scorer can fold
> it in as a fixed node penalty (not a user weight).

Backend code in `scoring.py:101–115` ignores any precomputed column and
recomputes the penalty from raw fields. So R3 does the work, R2 throws
it away.

**Fix — pick one:**
- (a) R3 emits precomputed `crossing_penalty`. `scoring.py:101–115`
  becomes `return float(seg.get("crossing_penalty") or 0.0)`.
  *Recommended* — keeps the formula in one place. The `accessible`
  ×2.5 multiplier stays in the backend (per TASKS.md line 40).
- (b) R3 drops `crossing_penalty`, just emits the raw inputs from
  Hard #2 (a), backend keeps its current formula.

Tied to the resolution of Hard #2.

### Hard #4 — stub bbox is not the Gillem corridor
**Files:** `backend/scripts/generate_stub_parquet.py:19`, `backend/app/models.py:13, 18`.

```python
# generate_stub_parquet.py:19
BBOX = (-84.42, 33.68, -84.33, 33.72)   # north of ATL airport
```

`corridor.json` (locked 2026-06-15) is:

```json
"bbox": [-84.37, 33.58, -84.29, 33.65]   // Gillem Logistics, Forest Park
```

These do not overlap — they're ~13 km apart. The example coordinates
in `models.py` (`origin: [-84.40, 33.69]`, `dest: [-84.35, 33.71]`)
are also in the wrong area.

**Fix:**
- Read `corridor.json` from the repo root in the stub generator
  (or hard-code the Gillem bbox).
- Update `models.py` examples to coordinates inside the Gillem bbox.
  Suggested: `origin: [-84.347, 33.610]` (Anvil Block Rd area),
  `dest: [-84.329, 33.620]` (`primary_destination.lonlat` from corridor.json).

If you actually *don't* mean to target Gillem — say so. That's a
corridor-choice conflict that supersedes everything else here.

### Hard #5 — sample parquet won't cover Mapbox routes
**Files:** `backend/app/segments.py:52–100`.

Your `snap_route` samples every ~15 m along the Mapbox route and finds
the nearest segment within 30 m. The sample parquet only covers ~500 m
around the Gillem entrance, so most realistic origins (a MARTA stop
~1.5 mi away) will produce **zero matched segments → `score = inf`**.

**Not a contract bug** — just deployment reality. To develop `/score`
end-to-end, either:
- (a) Test only with both origin + dest inside `data/sample_network.parquet`'s
  500 m radius (good enough for unit-shape work).
- (b) Wait for the full network (TASKS.md lines 12–17 — R3 has the
  pipeline; expanding from 500 m to full corridor is one CLI flag away).

R3 can produce a full-corridor parquet (no factor columns yet,
~30,723 segments / 716 km) in <1 min if you want it now for snap
development. Ask.

---

## Soft conflicts (work but ambiguous)

### Soft #1 — dead-code `slope_risk > 1.0` check
**File:** `backend/app/scoring.py:72–74`.

```python
slope = seg.get("slope_risk")
if slope is not None and slope > 1.0:
    return True
```

R3 contract: `slope_risk ∈ [0, 1]`, clipped. This branch never fires.
Steep-grade hard-avoids are surfaced via the `barrier` bool column
instead (per TASKS.md line 38).

**Fix:** drop the `slope > 1.0` branch; rely on `seg.get("barrier")`
(already checked on `scoring.py:66`).

### Soft #2 — `segment_id` used positionally, not as a key
**File:** `backend/app/segments.py:79–94`.

You track `matched_ids` as the positional `idx` returned by
`sindex.intersection`, then read `self.gdf.iloc[idx]`. Works today —
but if the GeoDataFrame is ever filtered, sorted, or reloaded between
startup and snap, positional matching drifts.

`segment_id` is the index on R3's parquet and unique by contract. Safer
pattern:

```python
seg_id = self.gdf.iloc[idx].segment_id
if seg_id in matched_ids:
    continue
matched_ids.add(seg_id)
row = self.gdf.loc[seg_id]   # keyed, drift-proof
```

### Soft #3 — `requirements.txt` floor drift
- Root `requirements.txt` (R3): `geopandas>=0.14, shapely>=2.0, pyarrow>=14, pandas>=2.0`.
- `backend/requirements.txt` (R2): `geopandas>=1.0.0, shapely>=2.0.0, pyarrow>=18.0.0`.

R2's floors are higher; harmless until someone tries running R2's code
in R3's venv. **Adopt R2's higher floors in the root file** — pyarrow
18 is the meaningful bump (better Parquet metadata round-tripping).

R3 will update root `requirements.txt` on the next R3 commit.

---

## Verified compatible (don't worry about these)

- **CRS:** both sides agree on `EPSG:4326` store / `EPSG:32616` math.
- **Geometry type:** `LineString` only, already exploded.
- **`sidewalk_cov` / `canopy_pct` semantics:** stored as coverage [0, 1];
  backend inverts to risk via `(1 - value)`. R3 will emit them the same way.
- **`highway`, `wheelchair`, `surface`** columns: backend reads as strings,
  R3 emits as strings. ✅
- **Factor-module interface:** R2 doesn't import `layers/*.py` — only reads
  the joined parquet. R3↔R4 own the interface; R2 is downstream.
- **`gdf.sindex` warm-up:** sample parquet warms in 0.1 ms — well under
  the <1 s contract from TASKS.md line 61.
- **Loader pattern:** `gpd.read_parquet(path)` + `gdf.sindex` matches
  what R3 uses in its own assertions.

---

## Five questions for R2 to answer

Once these are decided R3 unblocks the factor modules (`layers/sidewalk.py`
first). Reply on Slack or edit this file directly.

1. **Hard #1 — `lanes`/`maxspeed`/`width` coercion:** R3 will coerce to
   numeric (max of split values, NaN-safe) in the factor pipeline before
   you receive them. **Confirm?** (Y/N)
2. **Hard #2 — node tags on segments:** R3 emits `is_crossing` /
   `traffic_signals` / `crossing` as joined columns on segments, so your
   code stays. **Confirm?** (Or do you want a separate `crossings.parquet`?)
3. **Hard #3 — `crossing_penalty` ownership:** R3 emits the precomputed
   penalty; backend replaces `scoring.py:101–115` with one-line passthrough
   (×2.5 for `accessible` profile stays in backend). **Confirm?** (Or do
   you want to keep recomputing?)
4. **Hard #4 — bbox:** you're targeting Gillem per `corridor.json`,
   not West Atlanta. **Confirm?** If yes, update stub bbox + `models.py`
   examples. If no, we have a bigger conversation.
5. **Hard #5 — full network:** want R3 to produce a full-corridor parquet
   now (716 km, no factor columns yet) so you can test `snap_route`
   end-to-end? **(Y/N)** If yes, it ships within an hour.

---

## What R3 commits to next (regardless of answers)

- Lock the factor-module interface with R4 (`score(segments) -> Series`,
  no side effects, EPSG:4326 in / 32616 internal).
- Start `layers/sidewalk.py` — ARC layer is canonical (VALIDATION.md
  confirmed 94.7% OSM tags missing on Gillem).
- After Q1/Q2/Q3 above: numeric coercion for `lanes`/`maxspeed`/`width`
  in `prebake.py`, plus the chosen crossings approach.

If you have follow-up questions, leave them in a `## R2 notes` section
at the bottom of this file.

---

## R2 notes (2026-06-15)

Answers to the five questions above — implemented on `main`:

1. **Hard #1 — numeric coercion:** **Y.** Expect `lanes`/`maxspeed`/`width` as
   `float64 | NaN` in parquet. Stub generator updated to emit floats.
2. **Hard #2 — node tags on segments:** **Y (option a).** R3 emits
   `is_crossing` / `traffic_signals` / `crossing` as joined segment columns.
   Backend no longer reads them for scoring (see #3); kept available if needed
   for explanations later.
3. **Hard #3 — `crossing_penalty` ownership:** **Y (option a).** Backend now
   passthroughs `crossing_penalty` from parquet; `accessible` ×2.5 stays in
   `scoring.py`.
4. **Hard #4 — bbox:** **Y — Gillem.** Added `corridor.json` at repo root;
   stub generator reads its bbox; `models.py` examples updated to Gillem coords.
5. **Hard #5 — full network:** **N for now.** Gillem-bbox stub is enough for
   contract/shape work. Will request full-corridor parquet when testing
   `snap_route` against realistic MARTA-stop origins.
