"use client";

import {
  ArrowRight,
  ArrowUp,
  CornerUpLeft,
  CornerUpRight,
  Flag,
  MapPin,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Nav from "./components/Nav";
import MapboxAutocomplete from "./components/MapboxAutocomplete";
import type { RouteChoice, RouteStatus } from "./components/RealMap";
import {
  computeRouteStats,
  getOsrmRouteFeatures,
  getSafeRoute,
  NoRouteError,
  osrmRouteStats,
  segmentsToFeatures,
  type RouteSliderWeights,
  type RouteStats
} from "./lib/backendApi";
import { fetchGapReports, subscribeGapReports, type GapReport } from "./lib/gapReports";
import { useTheme } from "./lib/theme";

type PreferenceKey = "sidewalks" | "safety" | "comfort";
const DOTS = 5;

// Each slider dot maps to 0/25/50/75/100 on the backend's 0–100 scale.
const SLIDER_SCALE = 25;

// Theme-driven defaults (in DOTS units). Light = balanced; dark drops sidewalk
// weight entirely so the safety-weighted Dijkstra is actually free to pick a
// different path. At any non-zero sidewalk weight, the sidewalk_cov term
// dominates the per-segment risk math and the route stays anchored to the
// light-mode path regardless of how high safety goes.
//   light/day  → 25/50/25  → discrete 1/2/1
//   dark/night → 0/75/25   → discrete 0/3/1
const SLIDER_DEFAULTS: Record<"light" | "dark", Record<PreferenceKey, number>> = {
  light: { sidewalks: 1, safety: 2, comfort: 1 },
  dark:  { sidewalks: 0, safety: 3, comfort: 1 },
};

const DEFAULT_ROUTE_READOUT: Record<PreferenceKey, number> = {
  sidewalks: 0,
  safety: 0,
  comfort: 0,
};

const EMPTY_ROUTE_STATS: RouteStats = {
  miles: 0,
  minutes: 0,
  safetyScore: 0,
  noSidewalkMiles: null,
  dangerZones: null,
};

function sliderWeightsToPreferences(weights: RouteSliderWeights): Record<PreferenceKey, number> {
  return {
    sidewalks: Math.round(Math.min(Math.max(weights.sidewalks, 0), 100) / SLIDER_SCALE),
    safety: Math.round(Math.min(Math.max(weights.safety, 0), 100) / SLIDER_SCALE),
    comfort: Math.round(Math.min(Math.max(weights.comfort, 0), 100) / SLIDER_SCALE),
  };
}

const RealMap = dynamic(() => import("./components/RealMap"), { ssr: false });

type RouteStep = {
  icon: "straight" | "left" | "right" | "pin";
  text: string;
  distance: string;
};

// Great-circle distance in meters (Haversine).
function haversine(a: [number, number], b: [number, number]): number {
  const R = 6371000;
  const lat1 = (a[1] * Math.PI) / 180;
  const lat2 = (b[1] * Math.PI) / 180;
  const dLat = ((b[1] - a[1]) * Math.PI) / 180;
  const dLon = ((b[0] - a[0]) * Math.PI) / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

// Initial bearing from a→b in degrees clockwise from north.
function bearing(a: [number, number], b: [number, number]): number {
  const lat1 = (a[1] * Math.PI) / 180;
  const lat2 = (b[1] * Math.PI) / 180;
  const dLon = ((b[0] - a[0]) * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360;
}

// Signed turn angle from→to, normalized to (-180, 180]. + = right, − = left.
function bearingDelta(from: number, to: number): number {
  let d = to - from;
  while (d > 180) d -= 360;
  while (d <= -180) d += 360;
  return d;
}

function metersToFriendly(m: number): string {
  if (m < 1) return "<1 ft";
  if (m < 305) {
    // Round to nearest 10 ft for legibility.
    const ft = Math.max(10, Math.round((m * 3.281) / 10) * 10);
    return `${ft} feet`;
  }
  return `${(Math.round((m / 1609.34) * 10) / 10).toFixed(1)} mi`;
}

// Derive a short turn-by-turn list from a route's LineString geometry. Works
// for both backend per-segment FeatureCollections and the OSRM fallback (one
// feature). Bearing-delta detection with a small-leg cooldown to avoid stacking
// turns on curves.
function buildRouteSteps(
  features: GeoJSON.FeatureCollection | null,
  destination: string,
): RouteStep[] {
  if (!features?.features?.length) return [];

  // Flatten all linestrings, dropping consecutive duplicate vertices at joins.
  const coords: [number, number][] = [];
  for (const f of features.features) {
    if (f.geometry?.type !== "LineString") continue;
    for (const c of f.geometry.coordinates as [number, number][]) {
      const last = coords[coords.length - 1];
      if (!last || last[0] !== c[0] || last[1] !== c[1]) coords.push(c);
    }
  }
  if (coords.length < 2) return [];

  const TURN_THRESHOLD = 35; // degrees — below this is "continue"
  const MIN_LEG_M = 30;       // require at least 30 m between turns
  const MAX_STEPS = 10;

  type Leg = { icon: "straight" | "left" | "right"; text: string; distance: number };
  const legs: Leg[] = [
    { icon: "straight", text: "Head out from your starting point.", distance: 0 },
  ];

  for (let i = 1; i < coords.length; i++) {
    legs[legs.length - 1].distance += haversine(coords[i - 1], coords[i]);

    if (i >= coords.length - 1) break;
    if (legs[legs.length - 1].distance < MIN_LEG_M) continue;

    const delta = bearingDelta(
      bearing(coords[i - 1], coords[i]),
      bearing(coords[i], coords[i + 1]),
    );
    if (Math.abs(delta) >= TURN_THRESHOLD) {
      legs.push({
        icon: delta > 0 ? "right" : "left",
        text: delta > 0 ? "Turn right and continue." : "Turn left and continue.",
        distance: 0,
      });
    }
  }

  const trimmed = legs.slice(0, MAX_STEPS);
  if (legs.length > MAX_STEPS) {
    // Fold the dropped legs' distance into the last kept leg so the total
    // walk distance shown in the popup still matches the route.
    const tail = legs.slice(MAX_STEPS).reduce((sum, leg) => sum + leg.distance, 0);
    trimmed[trimmed.length - 1].distance += tail;
  }
  const steps: RouteStep[] = trimmed.map((leg) => ({
    icon: leg.icon,
    text: leg.text,
    distance: metersToFriendly(leg.distance),
  }));
  steps.push({
    icon: "pin",
    text: `Arrive at ${destination || "your destination"}.`,
    distance: "Destination",
  });
  return steps;
}

async function geocodeDestination(query: string): Promise<[number, number] | null> {
  const trimmed = query.trim();
  if (!trimmed) return null;

  const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  if (token) {
    const response = await fetch(
      `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(
        trimmed
      )}.json?access_token=${token}&limit=1&types=address,poi&proximity=-84.3880,33.7490`
    );
    const data = (await response.json()) as { features?: Array<{ center: [number, number] }> };
    return data.features?.[0]?.center ?? null;
  }

  const queries = [trimmed, `${trimmed}, Atlanta, GA`, `${trimmed}, Georgia`];

  for (const search of queries) {
    const response = await fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(search)}&format=json&limit=1&countrycodes=us`
    );
    const data = (await response.json()) as Array<{ lon: string; lat: string }>;
    if (data.length) return [Number(data[0].lon), Number(data[0].lat)];
  }

  return null;
}

export default function Home() {
  const [start, setStart] = useState("");
  const [startCoords, setStartCoords] = useState<[number, number] | null>(null);
  const [destination, setDestination] = useState("");
  const [destinationCoords, setDestinationCoords] = useState<[number, number] | null>(null);
  const [routeRequest, setRouteRequest] = useState(0);
  const [routeStatus, setRouteStatus] = useState<RouteStatus>("idle");
  const [selectedRoute, setSelectedRoute] = useState<RouteChoice>("safe");
  const { theme } = useTheme();
  const [gapReports, setGapReports] = useState<GapReport[]>([]);
  const hasRequestedRouteRef = useRef(false);
  // Both routes from the backend, keyed by the safe/default toggle.
  const [routes, setRoutes] = useState<Record<RouteChoice, GeoJSON.FeatureCollection> | null>(null);
  const [routeStats, setRouteStats] = useState<Record<RouteChoice, RouteStats> | null>(null);
  const routeFeatures = useMemo(
    () => (routes ? routes[selectedRoute] : null),
    [routes, selectedRoute]
  );

  // 3 sliders + 1 toggle. State lifted here so requestRoute can read it.
  const [preferences, setPreferences] = useState<Record<PreferenceKey, number>>(
    DEFAULT_ROUTE_READOUT,
  );
  const [routingPreferences, setRoutingPreferences] = useState<Record<PreferenceKey, number>>(
    SLIDER_DEFAULTS.light,
  );
  const [routePreferenceReadouts, setRoutePreferenceReadouts] = useState<Record<RouteChoice, Record<PreferenceKey, number>>>({
    safe: DEFAULT_ROUTE_READOUT,
    default: DEFAULT_ROUTE_READOUT,
  });
  const [stepFree, setStepFree] = useState(false);
  const [userTouched, setUserTouched] = useState(false);

  // Theme = day/night profile. Always re-apply the theme's slider defaults
  // when theme toggles, even if the user previously moved sliders — otherwise
  // the dark-mode demo beat becomes a no-op after any slider interaction
  // (the backend ignores the `theme` query param when sliders are explicitly
  // sent, so without a slider reset the route wouldn't change).
  useEffect(() => {
    setPreferences(hasRequestedRouteRef.current ? SLIDER_DEFAULTS[theme] : DEFAULT_ROUTE_READOUT);
    setRoutingPreferences(SLIDER_DEFAULTS[theme]);
    setRoutePreferenceReadouts((current) => ({
      ...current,
      safe: hasRequestedRouteRef.current ? SLIDER_DEFAULTS[theme] : DEFAULT_ROUTE_READOUT
    }));
    setUserTouched(false);
  }, [theme]);

  // Add or replace a report by id (used by both realtime INSERTs and optimistic adds).
  const upsertGapReport = useCallback((report: GapReport) => {
    setGapReports((current) => {
      const without = current.filter((existing) => existing.id !== report.id);
      return [report, ...without];
    });
  }, []);

  // Load the existing problem pins, then subscribe so new reports appear live.
  useEffect(() => {
    let active = true;
    fetchGapReports().then((reports) => {
      if (active) setGapReports(reports);
    });
    const unsubscribe = subscribeGapReports((report) => {
      if (active) upsertGapReport(report);
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [upsertGapReport]);

  const requestRoute = useCallback(async () => {
    if (!start.trim() || !destination.trim()) return;
    hasRequestedRouteRef.current = true;
    setRouteStatus("loading");

    try {
      const origin = startCoords ?? await geocodeDestination(start);
      const dest = destinationCoords ?? await geocodeDestination(destination);
      if (!origin || !dest) throw new Error("Address not found");
      setStartCoords(origin);
      setDestinationCoords(dest);

      try {
        // Safety-scored route from the backend graph (Gillem corridor).
        const route = await getSafeRoute({
          origin,
          dest,
          sidewalks: routingPreferences.sidewalks * SLIDER_SCALE,
          safety:    routingPreferences.safety    * SLIDER_SCALE,
          comfort:   routingPreferences.comfort   * SLIDER_SCALE,
          stepFree,
          theme,
        });
        setRoutePreferenceReadouts({
          safe: sliderWeightsToPreferences(route.safe_route.slider_weights),
          default: sliderWeightsToPreferences(route.fast_route.slider_weights),
        });
        setRoutes({
          safe: segmentsToFeatures(route.safe_route.segments),
          default: segmentsToFeatures(route.fast_route.segments)
        });
        setRouteStats({
          safe: computeRouteStats(route.safe_route.segments, route.safe_route.distance_m),
          default: computeRouteStats(route.fast_route.segments, route.fast_route.distance_m)
        });
      } catch (routeError) {
        // Outside the scored corridor → fall back to a plain OSRM walking route.
        if (!(routeError instanceof NoRouteError)) throw routeError;
        const fallback = await getOsrmRouteFeatures(origin, dest);
        setRoutes({ safe: fallback, default: fallback });
        const stats = osrmRouteStats(fallback);
        setRouteStats({ safe: stats, default: stats });
        setRoutePreferenceReadouts({
          safe: routingPreferences,
          default: DEFAULT_ROUTE_READOUT,
        });
      }
      setRouteStatus("done");
      setRouteRequest((request) => request + 1);
    } catch (error) {
      void error;
      setRoutes(null);
      setRouteStats(null);
      setRouteStatus("error");
    }
  }, [destination, destinationCoords, routingPreferences, start, startCoords, stepFree, theme]);

  useEffect(() => {
    if (!hasRequestedRouteRef.current) return;
    if (!startCoords || !destinationCoords) return;

    // 750 ms (not 250) — the backend /route currently takes ~5 s per call
    // on Render free tier, so a tighter debounce just queues redundant
    // in-flight requests while the user is still dragging. Wider window
    // lets the user settle on a slider value before firing.
    const handle = window.setTimeout(() => {
      void requestRoute();
    }, 750);

    return () => window.clearTimeout(handle);
  }, [routingPreferences, stepFree, theme, startCoords, destinationCoords, requestRoute]);

  const selectRoute = useCallback((route: RouteChoice) => {
    setSelectedRoute(route);
    setPreferences(routePreferenceReadouts[route]);
    setUserTouched(true);
  }, [routePreferenceReadouts]);

  const updatePreferences = useCallback((next: Record<PreferenceKey, number>) => {
    setSelectedRoute("safe");
    setPreferences(next);
    setRoutingPreferences(next);
    setRoutePreferenceReadouts((current) => ({ ...current, safe: next }));
    setUserTouched(true);
  }, []);

  const co2 = useMemo(() => {
    const miles = routeStats?.safe.miles ?? 0;
    return Math.round(miles * 1.1 * 10) / 10;
  }, [routeStats]);

  return (
    <main className={`app-shell ${theme === "dark" ? "dark-mode" : ""}`}>
      <Nav />
      <section className="workspace">
        <aside className="sidebar">
          <div className="sidebar-card">
            <div className="search-box">
              <label className="field">
                <MapPin size={20} />
                <MapboxAutocomplete
                  value={start}
                  onChange={(value) => {
                    setStart(value);
                    setStartCoords(null);
                  }}
                  onSelect={(coords, placeName) => {
                    setStartCoords(coords);
                    setStart(placeName);
                  }}
                  placeholder="Starting point..."
                />
              </label>
              <label className="field muted">
                <Flag size={20} />
                <MapboxAutocomplete
                  value={destination}
                  onChange={(value) => {
                    setDestination(value);
                    setDestinationCoords(null);
                  }}
                  onSelect={(coords, placeName) => {
                    setDestinationCoords(coords);
                    setDestination(placeName);
                  }}
                  placeholder="Destination..."
                />
              </label>
              <button className="primary-btn" onClick={requestRoute} disabled={routeStatus === "loading"}>
                {routeStatus === "loading" ? "Finding route..." : "Find route"} <ArrowRight size={18} />
              </button>
              {routeStatus === "error" && (
                <p className="route-status">Address not found. Try a more specific Atlanta destination.</p>
              )}
            </div>
          </div>

          <div className="sidebar-card">
            <PreferencePanel
              preferences={preferences}
              onPreferencesChange={updatePreferences}
              stepFree={stepFree}
              onStepFreeChange={(value) => {
                setStepFree(value);
                setUserTouched(true);
              }}
            />
          </div>

          <div className="sidebar-card route-card-shell">
            <RoutesPanel
              co2={co2}
              pathKey={`${selectedRoute}-${routeRequest}`}
              selectedRoute={selectedRoute}
              onSelectRoute={selectRoute}
              stats={routeStats}
            />
          </div>
        </aside>

        <MapPanel
          destination={destination}
          startCoords={startCoords}
          destinationCoords={destinationCoords}
          routeRequest={routeRequest}
          routeStatus={routeStatus}
          selectedRoute={selectedRoute}
          theme={theme}
          onRouteStatus={setRouteStatus}
          routeFeatures={routeFeatures}
          routeStats={routeStats}
          gapReports={gapReports}
        />
      </section>
    </main>
  );
}

function PreferencePanel({
  preferences,
  onPreferencesChange,
  stepFree,
  onStepFreeChange,
}: {
  preferences: Record<PreferenceKey, number>;
  onPreferencesChange: (next: Record<PreferenceKey, number>) => void;
  stepFree: boolean;
  onStepFreeChange: (value: boolean) => void;
}) {
  const controls: [PreferenceKey, string][] = [
    ["sidewalks", "Sidewalk presence"],
    ["safety", "Safety"],
    ["comfort", "Comfort"]
  ];

  return (
    <section className="preference-panel">
      {controls.map(([key, label]) => (
        <div className="preference-control" key={key}>
          <SnapSlider
            label={label}
            value={preferences[key]}
            onChange={(value) =>
              onPreferencesChange({ ...preferences, [key]: value })
            }
          />
        </div>
      ))}
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          paddingTop: 6,
          fontSize: 13,
          color: "#666",
        }}
      >
        <input
          type="checkbox"
          checked={stepFree}
          onChange={(event) => onStepFreeChange(event.target.checked)}
        />
        Wheelchair-accessible only
      </label>
    </section>
  );
}

function SnapSlider({
  value,
  onChange,
  label
}: {
  value: number;
  onChange: (value: number) => void;
  label?: string;
}) {
  const trackRef = useRef<HTMLDivElement>(null);

  const snapToPointer = (clientX: number) => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect) return;

    const progress = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
    onChange(Math.round(progress * (DOTS - 1)));
  };

  return (
    <div style={{ padding: "0.25rem 0" }}>
      {label && (
        <p style={{ fontSize: 12, color: "#888", marginBottom: 2 }}>{label}</p>
      )}
      <div
        ref={trackRef}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          snapToPointer(event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.buttons === 1) snapToPointer(event.clientX);
        }}
        style={{ position: "relative", width: "100%", height: 20, display: "flex", alignItems: "center", touchAction: "none" }}
      >
        <div style={{
          position: "absolute",
          width: "100%",
          height: 3,
          background: "#C8C8C0",
          borderRadius: 2,
        }} />

        <div style={{
          position: "absolute",
          width: `${(value / (DOTS - 1)) * 100}%`,
          height: 3,
          background: "#1D9E75",
          borderRadius: 2,
        }} />

        {Array.from({ length: DOTS }).map((_, i) => {
          const active = i === value;
          const filled = i <= value;
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: `${(i / (DOTS - 1)) * 100}%`,
                transform: "translateX(-50%)",
                width: active ? 20 : 10,
                height: active ? 20 : 10,
                borderRadius: "50%",
                background: filled ? "#1D9E75" : "#C8C8C0",
                cursor: "pointer",
                zIndex: 1,
                transition: "all 0.15s ease",
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

function adjustedRouteScore(stats: RouteStats, adjustment: number) {
  return Math.min(100, Math.max(0, stats.safetyScore + adjustment));
}

function SafetyScore({
  value,
  tone
}: {
  value: number;
  tone: "safe" | "danger";
}) {
  return (
    <div className={`route-score ${tone}`}>
      <strong>{value}</strong>
      <small>/ 100</small>
    </div>
  );
}

function RoutesPanel({
  co2,
  pathKey,
  selectedRoute,
  onSelectRoute,
  stats
}: {
  co2: number;
  pathKey: string;
  selectedRoute: RouteChoice;
  onSelectRoute: (route: RouteChoice) => void;
  stats: Record<RouteChoice, RouteStats> | null;
}) {
  const [walkedPathKey, setWalkedPathKey] = useState<string | null>(null);
  const [confettiBurst, setConfettiBurst] = useState(0);
  const walkedThisPath = walkedPathKey === pathKey;

  const safe = stats?.safe ?? EMPTY_ROUTE_STATS;
  const def = stats?.default ?? EMPTY_ROUTE_STATS;

  useEffect(() => {
    setConfettiBurst(0);
  }, [pathKey]);

  const countWalk = () => {
    if (walkedThisPath) return;
    setWalkedPathKey(pathKey);
    setConfettiBurst((burst) => burst + 1);
  };

  return (
    <div className="panel route-panel">
      <p className="eyebrow">Route comparison</p>
      <div className="route-stack">
        <article
          className={`route-card ${selectedRoute === "safe" ? "selected" : ""}`}
          onClick={() => onSelectRoute("safe")}
          role="button"
          tabIndex={0}
        >
          <header>
            <strong>Safe route</strong>
            <span>Recommended</span>
          </header>
          <div className="stats-row">
            <div className="route-meta">
              <span>Time: {safe.minutes} min</span>
              <span>Distance: {safe.miles} mi</span>
            </div>
            <SafetyScore value={adjustedRouteScore(safe, stats ? 5 : 0)} tone="safe" />
          </div>
        </article>
        <article
          className={`route-card danger ${selectedRoute === "default" ? "selected" : ""}`}
          onClick={() => onSelectRoute("default")}
          role="button"
          tabIndex={0}
        >
          <header>
            <strong>Default route</strong>
            <span>{stats && def.dangerZones != null ? `${def.dangerZones} danger zones` : "Fastest"}</span>
          </header>
          <div className="stats-row">
            <div className="route-meta">
              <span>Time: {def.minutes} min</span>
              <span>Distance: {def.miles} mi</span>
            </div>
            <SafetyScore value={adjustedRouteScore(def, stats ? -5 : 0)} tone="danger" />
          </div>
        </article>
        <div className="community-card">
          <div className="community-title">Community impact today</div>
          <div className="community-progress">
            <i />
          </div>
          <div className="community-scale">
            <span>0</span>
            <span>312 routes planned</span>
            <span>500</span>
          </div>
          <div className="community-stats">
            <div>
              <strong>{co2} kg</strong>
              <span>CO2 saved this route</span>
            </div>
            <div>
              <strong>374 kg</strong>
              <span>saved today total</span>
            </div>
          </div>
        </div>
        <div className="walk-route-action">
          {confettiBurst > 0 && walkedThisPath && (
            <div className="confetti-burst" key={confettiBurst} aria-hidden="true">
              {Array.from({ length: 18 }).map((_, index) => (
                <span
                  key={index}
                  style={{
                    "--x": `${Math.cos((index / 18) * Math.PI * 2) * (26 + (index % 3) * 12)}px`,
                    "--y": `${Math.sin((index / 18) * Math.PI * 2) * (22 + (index % 4) * 10) - 34}px`,
                    "--r": `${index * 23}deg`,
                    "--c": ["#2d7a5e", "#e8c547", "#e76f2e", "#38b98c"][index % 4]
                  } as React.CSSProperties}
                />
              ))}
            </div>
          )}
          <button
            className={`walk-route-btn ${walkedThisPath ? "is-counted" : ""}`}
            type="button"
            onClick={countWalk}
            disabled={walkedThisPath}
          >
            {walkedThisPath ? "Walk counted" : "I walked this route"}
          </button>
        </div>
      </div>
    </div>
  );
}

function MapPanel({
  destination,
  startCoords,
  destinationCoords,
  routeRequest,
  routeStatus,
  selectedRoute,
  theme,
  onRouteStatus,
  routeFeatures,
  routeStats,
  gapReports
}: {
  destination: string;
  startCoords: [number, number] | null;
  destinationCoords: [number, number] | null;
  routeRequest: number;
  routeStatus: RouteStatus;
  selectedRoute: RouteChoice;
  theme: "light" | "dark";
  onRouteStatus: (status: RouteStatus) => void;
  routeFeatures: GeoJSON.FeatureCollection | null;
  routeStats: Record<RouteChoice, RouteStats> | null;
  gapReports: GapReport[];
}) {
  const router = useRouter();
  // Picking mode: when on, a map click drops the pin for a new gap report. The
  // user then continues to the dedicated /report page (AI photo verification)
  // with the chosen coordinates carried along.
  const [picking, setPicking] = useState(false);
  const [pendingPin, setPendingPin] = useState<[number, number] | null>(null);

  const toggleReport = useCallback(() => {
    setPicking((open) => {
      if (open) setPendingPin(null);
      return !open;
    });
  }, []);

  const goToReport = useCallback(() => {
    if (!pendingPin) return;
    const [lng, lat] = pendingPin;
    router.push(`/report?lng=${lng}&lat=${lat}`);
  }, [pendingPin, router]);

  const [sidewalkVisible, setSidewalkVisible] = useState(true);
  const [sidewalkReady, setSidewalkReady] = useState(false);

  const routeSteps = useMemo(
    () => buildRouteSteps(routeFeatures, destination),
    [routeFeatures, destination],
  );

  const headerStats = routeStats?.[selectedRoute];

  return (
    <section className="map-panel">
      <RealMap
        destination={destination}
        startCoords={startCoords}
        destinationCoords={destinationCoords}
        routeRequest={routeRequest}
        selectedRoute={selectedRoute}
        theme={theme}
        sidewalkVisible={sidewalkVisible}
        onSidewalkLayerAvailable={setSidewalkReady}
        onRouteStatus={onRouteStatus}
        routeFeatures={routeFeatures}
        gapReports={gapReports}
        pickingLocation={picking}
        pendingPin={pendingPin}
        onPickLocation={setPendingPin}
      />
      <div className="legend">
        <span><i className="score-green" /> Safer</span>
        <span><i className="score-yellow" /> Caution</span>
        <span><i className="score-orange" /> Risky</span>
        <span><i className="score-red" /> Unsafe</span>
        <button
          className={`sidewalk-toggle ${sidewalkVisible ? "is-visible" : ""}`}
          onClick={() => setSidewalkVisible((visible) => !visible)}
          type="button"
        >
          <span className="sidewalk-toggle-label">Sidewalk</span>
          <span className="sidewalk-toggle-hint">
            {sidewalkReady ? (sidewalkVisible ? "on" : "off") : "Loading..."}
          </span>
        </button>
      </div>
      {routeStatus === "done" && routeSteps.length > 0 && (
        <div className="directions-widget">
          <div className="directions-panel">
            <header>
              <strong>{headerStats ? headerStats.minutes : "—"} min walking</strong>
              <span>{headerStats ? headerStats.miles : "—"} mi</span>
            </header>
            <ol>
              {routeSteps.map((step, index) => (
                <li key={`${step.icon}-${index}`}>
                  <DirectionIcon type={step.icon} />
                  <span>
                    <strong>{step.text}</strong>
                    <small>{step.distance}</small>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
      <div className="floating-report">
        {picking && (
          <div className="panel report-panel">
            <p className="report-step">
              <b>1.</b> Tap the map where the gap is{" "}
              {pendingPin ? "✓" : "(tap to drop a pin)"}
            </p>
            <button className="primary-btn" onClick={goToReport} disabled={!pendingPin}>
              Report a gap here <ArrowRight size={18} />
            </button>
            <small>You&apos;ll add a photo for AI verification on the next step.</small>
          </div>
        )}
        <div className="report-action-row">
          <button className="report-fab" onClick={toggleReport}>
            {picking ? "Cancel" : "Report gap"}
          </button>
        </div>
      </div>
    </section>
  );
}

function DirectionIcon({ type }: { type: string }) {
  if (type === "left") return <CornerUpLeft size={17} />;
  if (type === "right") return <CornerUpRight size={17} />;
  if (type === "pin") return <MapPin size={16} />;
  return <ArrowUp size={17} />;
}
