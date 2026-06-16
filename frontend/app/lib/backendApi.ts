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
