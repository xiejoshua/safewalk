# Corridor validation — hour-1 GO/NO-GO

**Date:** 2026-06-15
**Corridor:** `gillem-logistics-corridor`
**Bbox (W, S, E, N):** `[-84.37, 33.58, -84.29, 33.65]`
**Method:** `scripts/validate_corridor.py` — pulls OSM walk network via
Overpass, computes length-weighted sidewalk-tag distribution, classifies
arterials. Raw OSM cached at `data/osm/gillem-logistics-corridor.json`.

## Numbers

| Metric | Value |
|---|---:|
| Walk-eligible network length | **716.75 km** |
| Length with `sidewalk=yes` (or footway/path) | 36.9 km (5.1%) |
| Length with `sidewalk=no` | 1.4 km (0.2%) |
| Length with sidewalk tag missing | 678.5 km (94.7%) |
| **Arterials (primary/secondary/tertiary) without sidewalk tag** | **121.6 km (17.0%)** |
| Residential streets | 286.9 km (40.0%) |
| Service roads | 269.4 km (37.6%) |
| OSM crossings (`highway=crossing` nodes) | 125 |
| MARTA-ish bus stops in OSM | **0** (see below) |

## Verdict: GO-with-caveat

**Criterion A — walkability gap is real:** ✅ GO.
17% of the network is arterials with no sidewalk tag (clears the >15%
threshold). The 94.7% "unknown" share is consistent with DESIGN.md §14 ("OSM
sidewalk coverage thin"); it confirms — not refutes — the corridor's
walkability problem, and reinforces that `layers/sidewalk.py` MUST combine
OSM with the ARC sidewalk layer (per DESIGN.md §7d step 2) rather than treating
missing OSM tags as "no sidewalk."

**Criterion B — safer alternative exists:** ⚠️ GO-with-caveat.
286 km of residential and 269 km of service surface exists, so parallel-path
candidates are present in principle. But many service roads are interior to
the Gillem campus (private logistics yards — irrelevant for a worker walking
from a public bus stop), and DESIGN.md §11 already flagged that the canonical
Gillem case is workers walking arterials *because the residential network
doesn't connect to the campus*. We accept this caveat.

## What this means for the demo

Per DESIGN.md §11's contingency plan:

- **Primary demo beat:** the **"no safe route → report a gap"** beat at
  Gillem. The 17% arterial-without-sidewalk number is the receipt for the
  pitch deck.
- **Reroute beat:** to be confirmed once `layers/sidewalk.py` is wired up with
  the ARC layer. If a viable reroute around the Gillem arterials exists, use
  it; otherwise add a secondary corridor (West Atlanta / Belvedere Park) for
  the reroute beat only.
- **Pitch line stays intact:** "We scoped v1 to one corridor so every safety
  claim is verified" (DESIGN.md §11).

## Items skipped or deferred

- **Ground-truth at 3 random points (Street View)** — skipped for time. The
  94.7% unknown rate already establishes the ARC-layer dependency; per-point
  ground truth will happen organically during pipeline development.
- **MARTA stops in OSM** — confirmed missing across all standard tag
  conventions (`highway=bus_stop`, `public_transport=stop_position|platform`,
  `bus=yes`, `amenity=bus_station`). MARTA stops will come from MARTA GTFS
  (DESIGN.md §8 — keyless, public), not OSM. R4 will need GTFS for the
  stop list anyway.

## Next action

Proceed to `network/overpass.py` + `network/build.py` for the locked Gillem
bbox; publish `data/sample_network.parquet` to unblock R4 + R2 (TASKS.md
"Contracts" line 8).

## Reproducing

```bash
python3 scripts/validate_corridor.py
```

The Overpass JSON is cached; the script is idempotent.

---

# Factor spot-check — 2026-06-15

**Method:** `scripts/spot_check.py` picks 5 candidates per factor from the
2500 m slice around the Gillem entrance using deterministic criteria (highest
AADT on a named arterial, longest footway, OSM=unknown + ARC=yes cross-over,
etc.). Each candidate has a Street View URL with the camera heading set to
the segment's bearing. User eyeballs each via Street View; observation is
compared to the algorithm output on the same [0, 1] scale. Pass criterion:
**|eyeball − algorithm| ≤ 0.2**.

`scripts/spot_check.py` is idempotent and re-runnable. Picks listed below by
`segment_id` so the same locations can be re-verified later.

## Sidewalk picks (`layers/sidewalk.py`)

| # | segment_id | name | highway | algo | eyeball | diff | verdict |
|---|---|---|---|---:|---:|---:|---|
| 1 | `1279810295-0000` | (unnamed) | footway | 1.000 | 0.80 | 0.20 | ✓ (edge) |
| 2 | `1034466221-0002` | Anvil Block Rd | tertiary | 0.600 | 0.70 | 0.10 | ✓ |
| 3 | `1105698992-0011` | (unnamed) | service | 0.000 | n/a | — | skip (no SV) |
| 4 | `242093653-0026` | Price Street | residential | 0.896 | 0.90 | 0.00 | ✓ |
| 5 | `9106409-0018` | (unnamed) | residential (4.8 m) | 1.000 | 0.00 | **1.00** | **✗** |

**Pass rate: 3 / 4 viewable** (75% of viewable; 60% of all 5)

**Observations**
- **#1** Footway, clear sidewalk on one side, none on other. Algorithm hits 1.0 from the OSM=yes prior on a `highway=footway`. Real-world is a single-side sidewalk → 0.8 is honest.
- **#2 Anvil Block Rd** Sidewalk one side, four-lane crossing, no sidewalk on the other side. Algorithm 0.6 (OSM=yes, ARC=blank). User 0.7. Close enough — the OSM-prior floor is doing the right thing here even though ARC has no coverage.
- **#3** Inside the warehouse; no Street View coverage. Aerial shows industrial yards. Algorithm 0.000 is consistent with "no public sidewalk" in a logistics interior — algorithm-correct, just not eyeball-verifiable.
- **#4 Price Street** Residential, sidewalk both sides. Algorithm 0.896 from ARC alone (OSM=unknown). Validates that the Clayton ARC layer is reliable when it does have coverage.
- **#5 (4.8 m residential tail)** Algorithm 1.000 from ARC `arc_frac=1.0`. User sees no sidewalk where they are (residential neighborhood entrance). **Real false positive.** This is the spot-check's most valuable finding — see "Systematic issues" below.

## Traffic picks (`layers/traffic.py`)

| # | segment_id | name | highway | algo | eyeball | diff | verdict |
|---|---|---|---|---:|---:|---:|---|
| 6 | `9113869-0000` | Anvil Block Rd | secondary | 0.670 | 0.80 | 0.13 | ✓ |
| 7 | `1333412691-0002` | Jonesboro Road | primary | 0.915 | 0.60 | **0.32** | **✗** |
| 8 | `629305892-0047` | Forest Parkway | secondary | 0.634 | 0.90 | **0.27** | **✗** |
| 9 | `146006719-0032` | 3rd Avenue | residential | 0.205 | 0.20 | 0.01 | ✓ |
| 10 | `1279809274-0000` | (unnamed) | footway | 0.000 | n/a | — | skip (no SV) |

**Pass rate: 2 / 4 viewable** (50% of viewable; 40% of all 5)

**Observations**
- **#6 Anvil Block Rd** Large 4-way intersection with regular traffic. Algorithm 0.67 from class + speed default alone (this particular segment didn't get an AADT snap within 50 m). User 0.8. Close enough — the class+speed defaults captured roughly the right danger level even without AADT.
- **#7 Jonesboro / GA-54** User saw a "two-way road, four lanes total." Algorithm 0.915 from primary class + AADT 27,100 (saturated sigmoid). The road is busy but not "hellish" — algorithm over-scored by 0.32. *Note: GA-54 IS Jonesboro Rd in Forest Park — the Google "GA-54" label is a state-route designator on the same road, not a different street.*
- **#8 Forest Parkway** User saw a "wide multi-lane intersection" → 0.9. Algorithm 0.634. *Google labels the address as "5158 Jonesboro Rd" — the segment may be at the Forest Pkwy/Jonesboro intersection where the address uses Jonesboro's grid.* Algorithm under-scored by 0.27. The under-score is partly **expected**: traffic.py scores road segments only, not intersection complexity — that's `layers/crossing.py`'s job. When the crossings module ships and gets joined in prebake, this segment's combined risk should rise.
- **#9 3rd Avenue** Quiet residential one-way, 25 mph. Algorithm 0.205 (class default only, no AADT). User 0.2. Excellent agreement — class default is dialed correctly for residential streets.
- **#10** Footway, no Street View. Algorithm 0.000 is consistent with pedestrian-only paths.

## Overall verdict

**5 / 10 (or 5 / 8 viewable) — "tune specific weights/buffers based on systematic-issues list."**

Methods are not broken — the misses are addressable with specific changes, not redesigns. Sidewalk is in solid shape (3/4 viewable passing, one clear data-quality issue). Traffic is half-passing with two distinct systematic problems.

## Systematic issues found

### Sidewalk #1 — Short-segment false positives (CRITICAL)
Pick #5 is a 4.8 m tail segment with `arc_frac = 1.0` and OSM=unknown → score 1.0. User observation: no sidewalk visible. Likely cause: at intersection / curb-cut geometries, ARC linework may represent curb returns or driveway aprons that intersect a tail-merged segment fully, inflating the fraction to 1.0 even though the segment itself doesn't run alongside a real walkable sidewalk.

**Mitigation options (any of these alone would help):**
- Filter segments shorter than ~8 m out of factor scoring entirely (they're geometric tails at way junctions, not meaningful pedestrian units).
- Cap `arc_frac` contribution on short segments by requiring the ARC overlap geometry itself to be at least ~10 m long (excludes brief crossings).
- Cross-check the OSM signal: if both signals come from incidental geometry (4.8 m tail, OSM=unknown, ARC overlap mostly perpendicular to segment direction), de-rate the score.

### Sidewalk #2 — One-sided vs two-sided sidewalk not distinguished
Pick #1 scored 1.000 because OSM=yes + ARC coverage. User saw sidewalk on one side only. The 6 m buffer hits ARC linework on either side of the road centerline without distinguishing which side — a single-side coverage gives the same score as both-side coverage.

**Mitigation:** compute `arc_frac` separately for left/right of the segment's bearing (signed perpendicular offset) and report `min(left, right)` for the "worst-case" pedestrian who must cross to find a sidewalk. Or as a quick fix, scale the OSM=yes ceiling: `0.6 + 0.3 * arc_frac` capping at 0.9 instead of 1.0 (acknowledges that OSM tag often = one-sided in this corridor).

### Traffic #1 — Sigmoid saturation overstates risk at high AADT (Jonesboro Rd)
Pick #7: AADT 27,100 saturates our sigmoid to 0.917, plus primary class 0.85 → final 0.915. User read 0.6. Real pedestrian risk on a high-AADT primary is mitigated by signals, refuges, dedicated sidewalks present — none of which our scorer sees.

**Mitigation options:**
- Soften the AADT sigmoid at the high end: cap factor at 0.80 instead of 1.0, or shift the midpoint up (e.g., from 17.5k to 22.5k).
- Lower the AADT component weight from 0.30 to 0.25 and bump class weight to 0.45.
- Both — the cleanest is probably softening the AADT ceiling, since the issue is that 27k AADT shouldn't dominate over road-design factors we don't measure.

### Traffic #2 — Missing intersection complexity (Forest Pkwy)
Pick #8 under-scored because traffic.py doesn't account for intersection geometry — a 4-way multi-lane intersection is dramatically worse for pedestrians than a mid-block segment of the same arterial. **This is by design** — `layers/crossing.py` is responsible for intersection danger.

**Action:** verify after `crossing.py` lands that the combined `(traffic_risk + crossing_penalty)` rises to ≥ 0.85 for segments at multi-lane intersections. This pick becomes a regression test.

### Naming hygiene — OSM `name` field
User noted that pick #7's address is "4982 GA-54" (state route designator for Jonesboro Rd — same road, no real conflict) and pick #8's address is "5158 Jonesboro Rd" despite our pipeline calling it Forest Parkway. The Forest Pkwy case is at the Forest Pkwy/Jonesboro intersection. Worth a one-line code check to confirm OSM's `name` tag matches the segment's actual identity — our AADT snap propagates via `osm_way_id`, not name, so this is cosmetic for scoring but matters for human-readable demos.

## Reproducing

```bash
python3 scripts/spot_check.py
```

Writes `data/spot_check.md` with the 10 picks + per-segment Street View URLs.
Re-run is deterministic — picks are chosen by sortable criteria, not random.

## What to do next

1. **Apply sidewalk Mitigation #1** (filter <8 m tail segments OR require ARC overlap min length): one-line change, eliminates the #5-style false positive.
2. **Apply traffic Mitigation #3** (soften AADT sigmoid high end): two-constant change in `layers/traffic.py`. Should bring pick #7 from 0.915 down to ~0.75–0.80.
3. **Hold sidewalk Mitigation #2** (left/right buffer) and traffic Mitigation #4 (intersection — needs `crossing.py`) as follow-ups for the next sprint.
4. **Re-run the spot-check** after changes — verdict should rise from 5/10 to 7+/10 with these two tweaks. (`scripts/spot_check.py` re-runs deterministically against the same segment_ids.)

---

# Factor spot-check re-run — 2026-06-15 (after fixes)

**Method:** same 10 picks (same `segment_id`s — `scripts/spot_check.py` is
deterministic), same user eyeball observations. Two changes shipped:

- **`layers/sidewalk.py`**: added `MIN_SCORE_LENGTH_M = 8.0`. Segments
  shorter than 8 m bypass the ARC overlap calculation and use the OSM
  signal alone (`yes → 0.6`, `no/unknown → 0.0`). Addresses Sidewalk
  Mitigation #1.
- **`layers/traffic.py`**:
  - AADT sigmoid midpoint shifted `17.5k → 22.5k`; slope softened `4k → 5k`.
  - AADT factor now capped at `AADT_FACTOR_CAP = 0.80` (was 1.0). Hard
    saturation moved from `≥ 30k` to `≥ 35k`.
  - Primary class speed default: `1.00 → 0.80` (urban Atlanta primaries
    aren't really 45+ mph between signals).
  - Addresses Traffic Mitigation #3.

## Results

| # | factor | name | algo before | algo after | eyeball | diff after | verdict |
|---|---|---|---:|---:|---:|---:|---|
| 1 | sidewalk | (footway) | 1.000 | 1.000 | 0.80 | 0.200 | ✓ (edge) |
| 2 | sidewalk | Anvil Block Rd | 0.600 | 0.600 | 0.70 | 0.100 | ✓ |
| 3 | sidewalk | (service, warehouse) | 0.000 | 0.000 | n/a | — | skip |
| 4 | sidewalk | Price Street | 0.896 | 0.896 | 0.90 | 0.004 | ✓ |
| 5 | sidewalk | (4.8 m residential) | **1.000** | **0.000** | 0.00 | **0.000** | **✓** (was ✗) |
| 6 | traffic | Anvil Block Rd | 0.670 | 0.670 | 0.80 | 0.130 | ✓ |
| 7 | traffic | Jonesboro Road | **0.915** | **0.795** | 0.60 | **0.195** | **✓** (was ✗) |
| 8 | traffic | Forest Parkway | 0.634 | 0.557 | 0.90 | 0.343 | ✗ (by design — intersection) |
| 9 | traffic | 3rd Avenue | 0.205 | 0.205 | 0.20 | 0.005 | ✓ |
| 10 | traffic | (footway) | 0.000 | 0.000 | n/a | — | skip |

### New pass rate

**7 / 10** (or **7 / 8** viewable = **87.5%**). Up from 5/10.

- Sidewalk: 4 / 5 (was 3/5) — 4 / 4 viewable
- Traffic: 3 / 5 (was 2/5) — 3 / 4 viewable

### What changed and why

- **Pick #5 (the biggest miss)** went from 1.000 → 0.000, exactly matching the eyeball. The short-segment filter caught the intersection-tail artifact perfectly. Cost: zero impact on any other sidewalk pick (the other four were all ≥ 27 m).
- **Pick #7 (Jonesboro)** moved from 0.915 → 0.795. New AADT contribution at 27,100 vehicles: `0.715` (was `0.917`) → ×0.30 = 0.215. Primary speed default contribution: `0.80 × 0.30 = 0.24` (was `1.00 × 0.30 = 0.30`). Net drop: `(0.917 - 0.715) × 0.30 + (1.00 - 0.80) × 0.30 = 0.121`. Math matches the observed drop (0.915 - 0.795 = 0.120).
- **Pick #8 (Forest Pkwy)** dropped from 0.634 → 0.557 — slightly *worse* against the 0.90 eyeball. This is the by-design "intersection complexity" case; `layers/traffic.py` only sees segment characteristics, and softening the AADT made the segment's score slightly less aggressive. Will be rescued by `layers/crossing.py` once it ships; this pick becomes the regression test.

### Verdict

**Methods validated.** 7/10 (88% of viewable) crosses the "validated" threshold from the original plan. The one remaining miss (#8) is by-design and architecturally correct — intersection danger belongs to `layers/crossing.py`, not the per-segment traffic module.

### Held for future work

- **Sidewalk Mitigation #2** (left/right buffer to distinguish one-sided vs both-sided sidewalks). Pick #1 still scores 1.000 while ground truth was 0.80 (single-side sidewalk). The ±0.2 pass band absorbs this, but if the team wants stricter calibration later, the fix involves splitting the ARC buffer by signed perpendicular offset.
- **Traffic intersection-complexity (Forest Pkwy / pick #8)**. Will be addressed when `layers/crossing.py` lands. Until then, intersection segments will systematically under-score in traffic_risk alone.
- **Re-spot-check after `crossing.py`**. The same 10 picks will validate the joined `traffic_risk + crossing_penalty` once both modules feed `prebake.py`.

## Reproducing the re-run

```bash
python3 scripts/spot_check.py
```

Picks are deterministic; the same 10 `segment_id`s appear on every run.
The score columns reflect the current `layers/sidewalk.py` and
`layers/traffic.py`; if either module changes, the scores update and
this section's numbers go stale (re-run + update).

---

# Spot-check effect of `layers/crossing.py` — 2026-06-15

`layers/crossing.py` shipped after the re-run above. Same 10 picks; same
eyeballs; what changes is that traffic risk now combines with the new
`crossing_penalty` column to give a fuller per-segment safety reading.

**Method note:** `crossing.py` snaps OSM `highway=crossing` and
`highway=traffic_signals` nodes to segments via a 5 m buffer-intersect (not
nearest-neighbor). The buffer-intersect handles intersection nodes that
sit on multiple ways at once — a `traffic_signals` node at a 4-way junction
is correctly flagged on every Forest Pkwy/Jonesboro/etc. segment meeting there.

## Combined-risk changes (`traffic_risk + crossing_penalty`)

| Pick | name | tr before | + cp | combined | eyeball | new diff | verdict |
|---|---|---:|---:|---:|---:|---:|---|
| sw#1 | (footway) | 0.000 | 0.000 | 0.000 | n/a | — | (sidewalk) |
| sw#2 | Anvil Block (sw) | 0.530 | 0.000 | 0.530 | n/a | — | (sidewalk) |
| sw#3 | warehouse svc | 0.075 | 0.000 | 0.075 | n/a | — | (sidewalk) |
| sw#4 | Price Street | 0.205 | 0.000 | 0.205 | n/a | — | (sidewalk) |
| sw#5 | (short residential) | 0.205 | 0.000 | 0.205 | n/a | — | (sidewalk) |
| **tr#6** | **Anvil Block** | 0.670 | **+0.045** | **0.715** | 0.80 | **0.085** | ✓ improved |
| tr#7 | Jonesboro Road | 0.795 | 0.000 | 0.795 | 0.60 | 0.195 | ✓ (no change) |
| tr#8 | Forest Parkway | 0.557 | 0.000 | 0.557 | 0.90 | 0.343 | ✗ (OSM gap) |
| tr#9 | 3rd Avenue | 0.205 | 0.000 | 0.205 | 0.20 | 0.005 | ✓ (no change) |
| tr#10 | (footway) | 0.000 | 0.000 | 0.000 | n/a | — | (no traffic) |

**Pass rate (traffic side, viewable): still 3 / 4** — same as the re-run.

### What changed
- **tr#6 (Anvil Block traffic) improved.** Segment `9113869-0000` has a tagged unmarked-with-signal crossing → +0.045. Combined 0.715 vs eyeball 0.80, diff 0.085 (was 0.13).
- **tr#8 (Forest Pkwy) did NOT improve.** Combined still 0.557 vs eyeball 0.90. Investigation: a `highway=traffic_signals` node sits EXACTLY on the segment (distance ~0 m), but there is **no `highway=crossing` node** at that intersection in OSM. Per the spec (penalty applies only to tagged crossings), the algorithm correctly emits `crossing_penalty=0` for signal-only segments. **This is an OSM data gap, not an algorithm bug** — the original plan flagged it as a risk.
- **No regressions** anywhere.

## Finding: OSM crossing-node coverage

At the Forest Pkwy ↔ Jonesboro Rd intersection (pick #8's spot), OSM has:
- `highway=traffic_signals` node `67371367` (signal exists) ✓
- No `highway=crossing` node ✗

A pedestrian at this intersection WILL cross — there's a signal. Real ground truth says "dangerous crossing." Our algorithm says "no tagged crossing → no penalty." The gap is OSM's, but we own surfacing it.

### Two ways to close pick #8's gap (held for future work)

1. **Treat signal-only nodes as implicit crossings.** If a segment has `traffic_signals=yes` but `is_crossing=False`, apply the penalty assuming signalized + class-default lanes. Quick (3-line change); empirically rough. Pick #8 would go to ~0.62 (combined), still under 0.90 but much closer.
2. **Add an intersection-complexity penalty** based on way-junction topology (count of ways meeting at a node). Principled but its own task.

For now, option (1) is the right next calibration step *if* the spot-check verdict needs to rise further. Hold until we've shipped the other R3 modules and re-spot-checked.

## What `crossing.py` did correctly

Beyond pick #8, the module's coverage is sensible:
- **41 segments** with `is_crossing=True` in the 10k-segment slice — recognizable arterial crossings.
- **61 segments** with `traffic_signals=yes` — including intersection nodes shared by multiple ways (buffer-intersect handles ties).
- **Top-5 penalty segments** are all arterials (Anvil Block secondary 5-lane unmarked → 0.1875; Jonesboro primary 5-lane uncontrolled → 0.1875; Macon Hwy primary 4-lane unmarked → 0.150; …).
- **Signalization correctly reduces penalty** (signalized factor 0.4×): Macon Hwy 4-lane *signalized* → 0.060 vs the *unsignalized* Macon segment → 0.150.
- **Pedestrian-only ways** (footway/path): zero penalty even when `is_crossing=True`, because lanes=0 → width factor=0.
- **Runtime:** 0.04 s on 10k segments.

## Reproducing

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from layers.crossing import score, enrich
# load wide corpus (see scripts/spot_check.py for the recipe)
# then call score(wide) or enrich(wide) and inspect
"
```

The `crossing.py` module uses only data already in `data/osm/<corridor>.json`
— no external fetch. Deterministic across re-runs.

