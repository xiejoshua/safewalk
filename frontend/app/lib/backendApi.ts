import { routeData } from "./data";

export type LngLat = [number, number];
export type Theme = "light" | "dark";

export type ScoreRequest = {
  origin: LngLat;
  dest: LngLat;
  sidewalks?: number;          // 0–100; defaults via theme if omitted
  safety?: number;             // 0–100
  comfort?: number;            // 0–100
  step_free?: boolean;         // wheelchair-accessible toggle
  theme?: Theme;               // light=day defaults, dark=night defaults
};

export type ScoredRoute = {
  score: number;
  minutes: number;
  geojson: GeoJSON.FeatureCollection;
};

export type ScoreResponse = {
  safest: ScoredRoute;
  alternatives: ScoredRoute[];
};

export type SafeRouteRequest = {
  origin: LngLat;
  dest: LngLat;
  sidewalks: number; // 0–100
  safety: number; // 0–100
  comfort: number; // 0–100
  stepFree: boolean;
  theme: Theme;
};

export type RouteApiSegment = {
  segment_id: string;
  risk: number | null;
  sidewalk_cov: number | null;
  length_m: number | null;
  geometry: GeoJSON.LineString | null;
};

export type RouteStats = {
  miles: number;
  minutes: number;
  noSidewalkMiles: number | null; // null = unknown (OSRM fallback, outside corridor)
  dangerZones: number | null;
};

export type RouteSliderWeights = {
  sidewalks: number;
  safety: number;
  comfort: number;
};

export type SafeRouteResponse = {
  safe_route: {
    segments: RouteApiSegment[];
    total_risk: number;
    distance_m: number;
    explanation: string;
    slider_weights: RouteSliderWeights;
  };
  fast_route: {
    segments: RouteApiSegment[];
    distance_m: number;
    slider_weights: RouteSliderWeights;
  };
};

// Thrown when the backend can't connect origin and destination on the walkable graph.
export class NoRouteError extends Error {
  constructor(message = "No safe route found") {
    super(message);
    this.name = "NoRouteError";
  }
}

export type VerifyGapRequest = {
  photo: File;
  coordinates: LngLat;
  note?: string;
};

export type SubmitGapReportRequest = {
  photo?: File | null;
  coordinates: LngLat;
  note?: string;
  type?: string;
};

export type VerifiedGapReport = {
  id: string;
  type: string;
  note: string | null;
  photo_url: string | null;
  lng: number;
  lat: number;
  status: string | null;
  reported_at: string | null;
};

export type VerifyGapResponse = {
  verified: boolean;
  confidence?: number;
  report?: VerifiedGapReport;
  reason?: string;
  ai_type?: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_SAFEWALK_API_URL;

function mockRouteGeojson(): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { source: "frontend-mock" },
        geometry: {
          type: "LineString",
          coordinates: routeData.safe_route.geometry.map(([lat, lng]) => [lng, lat])
        }
      }
    ]
  };
}

export async function getBackendHealth() {
  if (!API_BASE_URL) return { status: "mocked" };

  const response = await fetch(`${API_BASE_URL}/health`);
  return response.json() as Promise<{ status: string }>;
}

export async function scoreRoute(request: ScoreRequest): Promise<ScoreResponse> {
  if (!API_BASE_URL) {
    return {
      safest: {
        score: 0.18,
        minutes: routeData.safe_route.duration_min,
        geojson: mockRouteGeojson()
      },
      alternatives: [
        {
          score: 0.41,
          minutes: routeData.default_route.duration_min,
          geojson: mockRouteGeojson()
        }
      ]
    };
  }

  const response = await fetch(`${API_BASE_URL}/score`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });

  if (!response.ok) throw new Error("Failed to score route");
  return response.json() as Promise<ScoreResponse>;
}

// Get a safety-scored walking route from the backend graph router (uses scoring.py,
// no Mapbox needed). Throws NoRouteError on a 404 (origin/dest don't connect).
export async function getSafeRoute(request: SafeRouteRequest): Promise<SafeRouteResponse> {
  if (!API_BASE_URL) {
    throw new Error("Backend not configured. Set NEXT_PUBLIC_SAFEWALK_API_URL to route.");
  }

  const params = new URLSearchParams({
    origin_lat: String(request.origin[1]),
    origin_lng: String(request.origin[0]),
    dest_lat: String(request.dest[1]),
    dest_lng: String(request.dest[0]),
    sidewalks: String(Math.round(request.sidewalks)),
    safety: String(Math.round(request.safety)),
    comfort: String(Math.round(request.comfort)),
    step_free: String(request.stepFree),
    theme: request.theme
  });

  const response = await fetch(`${API_BASE_URL}/route?${params.toString()}`);
  if (response.status === 404) throw new NoRouteError();
  if (!response.ok) throw new Error("Failed to fetch route");
  return response.json() as Promise<SafeRouteResponse>;
}

const METERS_PER_MILE = 1609.34;
const WALK_MPS = 1.34; // ~3 mph walking pace
const NO_SIDEWALK_COV = 0.25; // below this a segment counts as "no sidewalk"
const DANGER_RISK = 0.6; // segment risk above this is a "danger" segment

// Real per-route stats from the backend graph route: distance, walk time, miles
// without a sidewalk, and number of danger zones (contiguous high-risk runs).
export function computeRouteStats(segments: RouteApiSegment[], distanceM: number): RouteStats {
  let noSidewalkM = 0;
  let dangerZones = 0;
  let inDanger = false;

  for (const seg of segments) {
    const len = seg.length_m ?? 0;
    if ((seg.sidewalk_cov ?? 1) < NO_SIDEWALK_COV) noSidewalkM += len;
    const risky = (seg.risk ?? 0) >= DANGER_RISK;
    if (risky && !inDanger) dangerZones += 1;
    inDanger = risky;
  }

  return {
    miles: Math.round((distanceM / METERS_PER_MILE) * 10) / 10,
    minutes: Math.max(1, Math.round(distanceM / WALK_MPS / 60)),
    noSidewalkMiles: Math.round((noSidewalkM / METERS_PER_MILE) * 10) / 10,
    dangerZones
  };
}

// Distance-only stats for an OSRM fallback route (no per-segment safety data).
export function osrmRouteStats(features: GeoJSON.FeatureCollection): RouteStats {
  let meters = 0;
  for (const f of features.features) {
    if (f.geometry?.type !== "LineString") continue;
    const c = f.geometry.coordinates;
    for (let i = 1; i < c.length; i++) {
      const [lng1, lat1] = c[i - 1];
      const [lng2, lat2] = c[i];
      const dLat = ((lat2 - lat1) * Math.PI) / 180;
      const dLng = ((lng2 - lng1) * Math.PI) / 180;
      const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
      meters += 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }
  }
  return {
    miles: Math.round((meters / METERS_PER_MILE) * 10) / 10,
    minutes: Math.max(1, Math.round(meters / WALK_MPS / 60)),
    noSidewalkMiles: null,
    dangerZones: null
  };
}

// Convert backend route segments into a gradient FeatureCollection for the map. Each
// segment becomes a LineString whose `score` (0=unsafe..100=safe) drives the color ramp.
// Works for both safe_route and fast_route (both now carry per-segment risk).
export function segmentsToFeatures(segments: RouteApiSegment[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: segments
      .filter((segment): segment is RouteApiSegment & { geometry: GeoJSON.LineString } =>
        segment.geometry != null
      )
      .map((segment) => ({
        type: "Feature",
        properties: { score: Math.round((1 - (segment.risk ?? 0)) * 100) },
        geometry: segment.geometry
      }))
  };
}

// Fallback router for points outside the scored Gillem corridor: OSRM gives a
// walking path anywhere. It carries no per-segment safety score, so the whole line
// renders in one neutral color (score 70). Used when /route returns NoRouteError.
export async function getOsrmRouteFeatures(
  origin: LngLat,
  dest: LngLat
): Promise<GeoJSON.FeatureCollection> {
  const coords = `${origin.join(",")};${dest.join(",")}`;
  const response = await fetch(
    `https://router.project-osrm.org/route/v1/foot/${coords}?overview=full&geometries=geojson`
  );
  if (!response.ok) throw new Error("Failed to fetch route geometry");
  const data = await response.json();
  const coordinates = data.routes?.[0]?.geometry?.coordinates as [number, number][] | undefined;
  if (!coordinates?.length) throw new Error("Missing route geometry");

  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { score: 70 },
        geometry: { type: "LineString", coordinates }
      }
    ]
  };
}

// Upload a gap photo to the backend, which runs Claude vision verification. If the
// AI confirms a real hazard, the backend inserts it into Supabase and the new pin
// arrives on every open map via the realtime subscription.
export async function verifyGapReport(request: VerifyGapRequest): Promise<VerifyGapResponse> {
  if (!API_BASE_URL) {
    throw new Error(
      "Backend not configured. Set NEXT_PUBLIC_SAFEWALK_API_URL to enable gap reporting."
    );
  }

  const [lng, lat] = request.coordinates;
  const form = new FormData();
  form.append("photo", request.photo);
  form.append("lng", String(lng));
  form.append("lat", String(lat));
  if (request.note) form.append("note", request.note);

  const response = await fetch(`${API_BASE_URL}/verify-gap`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    let detail = "Failed to verify gap report";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }

  return response.json() as Promise<VerifyGapResponse>;
}

export async function submitGapReport(request: SubmitGapReportRequest): Promise<VerifyGapResponse> {
  if (request.photo) {
    return verifyGapReport({
      photo: request.photo,
      coordinates: request.coordinates,
      note: request.note
    });
  }

  if (!API_BASE_URL) {
    throw new Error(
      "Backend not configured. Set NEXT_PUBLIC_SAFEWALK_API_URL to enable gap reporting."
    );
  }

  const [lng, lat] = request.coordinates;
  const response = await fetch(`${API_BASE_URL}/gap-reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: request.type ?? "other",
      note: request.note ?? "",
      lng,
      lat
    })
  });

  if (!response.ok) {
    let detail = "Failed to submit gap report";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }

  return {
    verified: true,
    report: (await response.json()) as VerifiedGapReport
  };
}
