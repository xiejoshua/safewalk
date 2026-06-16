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

export type GapReportRequest = {
  coordinates: LngLat;
  type: string;
  note?: string;
};

export type GapReportResponse = {
  id: string;
  status: "mocked" | "submitted";
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

export async function submitGapReport(report: GapReportRequest): Promise<GapReportResponse> {
  void report;

  return {
    id: "ATL-2026-0417",
    status: "mocked"
  };
}
