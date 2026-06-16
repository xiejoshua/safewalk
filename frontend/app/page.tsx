"use client";

import {
  AlertTriangle,
  ArrowRight,
  ArrowUp,
  Check,
  Clock3,
  CornerUpLeft,
  CornerUpRight,
  Flag,
  Footprints,
  MapPin,
  Shield,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapboxAutocomplete from "./components/MapboxAutocomplete";
import type { RouteChoice, RouteStatus } from "./components/RealMap";
import {
  computeRouteStats,
  getOsrmRouteFeatures,
  getSafeRoute,
  NoRouteError,
  osrmRouteStats,
  segmentsToFeatures,
  submitGapReport as submitGapReportToBackend,
  type RouteStats
} from "./lib/backendApi";
import { fetchGapReports, gapTypeMeta, subscribeGapReports, type GapReport } from "./lib/gapReports";
import { routeData, scoreData } from "./lib/data";

type PreferenceKey = "sidewalks" | "safety" | "comfort";
const DOTS = 5;

// Each slider dot maps to 0/25/50/75/100 on the backend's 0–100 scale.
const SLIDER_SCALE = 25;

// Theme-driven defaults (in DOTS units). Match DESIGN.md §7c roughly:
//   light/day  → sidewalks 30, safety 55, comfort 15  → discrete 1/2/1
//   dark/night → sidewalks 20, safety 70, comfort 10  → discrete 1/3/0
const SLIDER_DEFAULTS: Record<"light" | "dark", Record<PreferenceKey, number>> = {
  light: { sidewalks: 1, safety: 2, comfort: 1 },
  dark:  { sidewalks: 1, safety: 3, comfort: 0 },
};

const RealMap = dynamic(() => import("./components/RealMap"), { ssr: false });

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
  const [tab, setTab] = useState<"routes" | "score">("routes");
  const [start, setStart] = useState("");
  const [startCoords, setStartCoords] = useState<[number, number] | null>(null);
  const [destination, setDestination] = useState("");
  const [destinationCoords, setDestinationCoords] = useState<[number, number] | null>(null);
  const [routeRequest, setRouteRequest] = useState(0);
  const [routeStatus, setRouteStatus] = useState<RouteStatus>("idle");
  const [selectedRoute, setSelectedRoute] = useState<RouteChoice>("safe");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [gapReports, setGapReports] = useState<GapReport[]>([]);
  // Both routes from the backend, keyed by the safe/default toggle.
  const [routes, setRoutes] = useState<Record<RouteChoice, GeoJSON.FeatureCollection> | null>(null);
  const [routeStats, setRouteStats] = useState<Record<RouteChoice, RouteStats> | null>(null);
  const routeFeatures = useMemo(
    () => (routes ? routes[selectedRoute] : null),
    [routes, selectedRoute]
  );

  // 3 sliders + 1 toggle. State lifted here so requestRoute can read it.
  const [preferences, setPreferences] = useState<Record<PreferenceKey, number>>(
    SLIDER_DEFAULTS.light,
  );
  const [stepFree, setStepFree] = useState(false);
  const [userTouched, setUserTouched] = useState(false);

  // Theme change resets slider defaults UNTIL the user has moved a slider.
  // Option α from DESIGN: light/dark theme is also the day/night profile.
  useEffect(() => {
    if (userTouched) return;
    setPreferences(SLIDER_DEFAULTS[theme]);
  }, [theme, userTouched]);

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
          sidewalks: preferences.sidewalks * SLIDER_SCALE,
          safety:    preferences.safety    * SLIDER_SCALE,
          comfort:   preferences.comfort   * SLIDER_SCALE,
          stepFree,
          theme,
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
      }
      setRouteStatus("done");
      setRouteRequest((request) => request + 1);
    } catch (error) {
      void error;
      setRoutes(null);
      setRouteStats(null);
      setRouteStatus("error");
    }
  }, [destination, destinationCoords, preferences, start, startCoords, stepFree, theme]);

  const co2 = useMemo(() => {
    const miles = routeStats?.safe.miles ?? routeData.safe_route.distance_mi;
    return Math.round(miles * 1.1 * 10) / 10;
  }, [routeStats]);

  return (
    <main className={`app-shell ${theme === "dark" ? "dark-mode" : ""}`}>
      <Nav theme={theme} onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))} />
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
              onPreferencesChange={(next) => {
                setPreferences(next);
                setUserTouched(true);
              }}
              stepFree={stepFree}
              onStepFreeChange={(value) => {
                setStepFree(value);
                setUserTouched(true);
              }}
            />
          </div>

          <div className="sidebar-card route-card-shell">
            <div className="tabs">
              {[
                ["routes", "Routes"],
                ["score", "Safety score"]
              ].map(([id, label]) => (
                <button
                  key={id}
                  className={tab === id ? "active" : ""}
                  onClick={() => setTab(id as typeof tab)}
                >
                  {label}
                </button>
              ))}
            </div>

            {tab === "routes" && (
              <RoutesPanel
                co2={co2}
                pathKey={`${selectedRoute}-${routeRequest}`}
                selectedRoute={selectedRoute}
                onSelectRoute={setSelectedRoute}
                stats={routeStats}
              />
            )}
            {tab === "score" && <ScorePanel />}
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
          gapReports={gapReports}
          onNewReport={upsertGapReport}
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

function Nav({
  theme,
  onToggleTheme
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
}) {
  return (
    <nav className="nav">
      <div className="brand">
        <span><Footprints size={21} /></span>
        Safewalk
      </div>
      <div className="nav-links">
        <a href="/">Map</a>
        <a href="/status">Status</a>
        <a>About</a>
      </div>
      <button className={`theme-toggle ${theme === "dark" ? "is-dark" : ""}`} onClick={onToggleTheme} type="button">
        <svg
          aria-hidden="true"
          className="theme-toggle-icon"
          fill="currentColor"
          strokeLinecap="round"
          viewBox="0 0 32 32"
        >
          <g>
            <circle className="theme-toggle-core" cx="16" cy="16" />
            <circle className="theme-toggle-cutout" cx="21" cy="11" r="8" />
            <g className="theme-toggle-rays" stroke="currentColor" strokeWidth="1.5">
              <path d="M16 5.5v-4" />
              <path d="M16 30.5v-4" />
              <path d="M1.5 16h4" />
              <path d="M26.5 16h4" />
              <path d="m23.4 8.6 2.8-2.8" />
              <path d="m5.7 26.3 2.9-2.9" />
              <path d="m5.8 5.8 2.8 2.8" />
              <path d="m23.4 23.4 2.9 2.9" />
            </g>
          </g>
        </svg>
        {theme === "dark" ? "Light" : "Dark"}
      </button>
    </nav>
  );
}

function sidewalkStat(s?: RouteStats) {
  if (!s || s.noSidewalkMiles == null) return <span>Sidewalk: n/a</span>;
  if (s.noSidewalkMiles <= 0) return <span className="ok"><Check size={15} /> Full sidewalk</span>;
  return <span className="bad">x {s.noSidewalkMiles} mi no sidewalk</span>;
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

  const safe = stats?.safe;
  const def = stats?.default;

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
            <strong><Shield size={18} /> Safe route</strong>
            <span>Recommended</span>
          </header>
          <div className="stats-row">
            <span><Clock3 size={15} /> Time: {safe ? safe.minutes : routeData.safe_route.duration_min} min</span>
            <span>Distance: {safe ? safe.miles : routeData.safe_route.distance_mi} mi</span>
            {sidewalkStat(safe)}
          </div>
        </article>
        <article
          className={`route-card danger ${selectedRoute === "default" ? "selected" : ""}`}
          onClick={() => onSelectRoute("default")}
          role="button"
          tabIndex={0}
        >
          <header>
            <strong><AlertTriangle size={18} /> Default route</strong>
            <span>{def && def.dangerZones != null ? `${def.dangerZones} danger zones` : "Fastest"}</span>
          </header>
          <div className="stats-row">
            <span><Clock3 size={15} /> Time: {def ? def.minutes : routeData.default_route.duration_min} min</span>
            <span>Distance: {def ? def.miles : routeData.default_route.distance_mi} mi</span>
            {sidewalkStat(def)}
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

function ScorePanel() {
  const rows = [
    ["Sidewalk", scoreData.sidewalk],
    ["Traffic risk", scoreData.traffic_speed],
    ["Crash history", scoreData.crash_history],
    ["Accessible", scoreData.accessible]
  ] as const;

  return (
    <div className="panel score-panel">
      <div className="score-ring" style={{ "--score": scoreData.overall } as React.CSSProperties}>
        <span>{scoreData.overall}</span>
        <small>/100</small>
      </div>
      {rows.map(([label, value]) => (
        <div className="bar-row" key={label}>
          <span>{label}</span>
          <div><i className={value >= 70 ? "green" : value >= 50 ? "amber" : "red"} style={{ width: `${value}%` }} /></div>
          <b>{value}</b>
        </div>
      ))}
    </div>
  );
}

type ReportStatus = "idle" | "verifying" | "verified" | "rejected" | "error";

function ReportPanel({
  pendingPin,
  photoPreview,
  hasPhoto,
  note,
  setNote,
  onPhotoSelected,
  status,
  message,
  onSubmit
}: {
  pendingPin: [number, number] | null;
  photoPreview: string | null;
  hasPhoto: boolean;
  note: string;
  setNote: (value: string) => void;
  onPhotoSelected: (file: File | null) => void;
  status: ReportStatus;
  message: string;
  onSubmit: () => void;
}) {
  const canSubmit = Boolean(pendingPin) && status !== "verifying";

  return (
    <div className="panel report-panel">
      <p className="report-step">
        <b>1.</b> Tap the map to mark the spot{" "}
        {pendingPin ? "✓" : "(tap to drop a pin)"}
      </p>
      <p className="report-step">
        <b>2.</b> Add a photo of the gap (optional)
      </p>
      <input
        className="report-photo-input"
        type="file"
        accept="image/*"
        capture="environment"
        onChange={(event) => onPhotoSelected(event.target.files?.[0] ?? null)}
      />
      {photoPreview && <img className="report-thumb" src={photoPreview} alt="Gap preview" />}
      <input
        className="report-note-input"
        type="text"
        placeholder="Add a note (optional)"
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
      <button className="primary-btn" onClick={onSubmit} disabled={!canSubmit}>
        {status === "verifying"
          ? hasPhoto ? "Verifying photo..." : "Submitting report..."
          : "Submit report"} <ArrowRight size={18} />
      </button>
      {message && (
        <p
          className={`report-status ${
            status === "verified" ? "ok" : status === "rejected" || status === "error" ? "bad" : ""
          }`}
        >
          {message}
        </p>
      )}
      <small>Anonymous · geotagged · live on the map</small>
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
  gapReports,
  onNewReport
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
  gapReports: GapReport[];
  onNewReport: (report: GapReport) => void;
}) {
  const [reportOpen, setReportOpen] = useState(false);
  const [pendingPin, setPendingPin] = useState<[number, number] | null>(null);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [reportStatus, setReportStatus] = useState<ReportStatus>("idle");
  const [reportMessage, setReportMessage] = useState("");

  const resetReport = useCallback(() => {
    setReportOpen(false);
    setPendingPin(null);
    setNote("");
    setReportStatus("idle");
    setReportMessage("");
    setPhotoFile(null);
    setPhotoPreview((current) => {
      if (current) URL.revokeObjectURL(current);
      return null;
    });
  }, []);

  const choosePhoto = useCallback((file: File | null) => {
    setReportStatus("idle");
    setReportMessage("");
    setPhotoFile(file);
    setPhotoPreview((current) => {
      if (current) URL.revokeObjectURL(current);
      return file ? URL.createObjectURL(file) : null;
    });
  }, []);

  const submitReport = useCallback(async () => {
    if (!pendingPin) return;
    setReportStatus("verifying");
    setReportMessage(photoFile ? "Claude is analyzing your photo..." : "Submitting your report...");
    try {
      const result = await submitGapReportToBackend({
        photo: photoFile,
        coordinates: pendingPin,
        note: note.trim() || undefined
      });
      if (result.verified && result.report) {
        onNewReport(result.report);
        setReportStatus("verified");
        setReportMessage(
          photoFile
            ? `AI confirmed: ${gapTypeMeta(result.report.type).label}. Pin is live on the map.`
            : "Report submitted. Pin is live on the map."
        );
        window.setTimeout(resetReport, 2200);
      } else {
        setReportStatus("rejected");
        setReportMessage(result.reason ?? "Couldn't confirm a gap. Try a clearer photo.");
      }
    } catch (error) {
      setReportStatus("error");
      setReportMessage(error instanceof Error ? error.message : "Something went wrong.");
    }
  }, [note, onNewReport, pendingPin, photoFile, resetReport]);

  const toggleReport = useCallback(() => {
    setReportOpen((open) => {
      if (open) resetReport();
      return !open;
    });
  }, [resetReport]);

  const [sidewalkVisible, setSidewalkVisible] = useState(true);
  const [sidewalkReady, setSidewalkReady] = useState(false);

  const routeSteps = selectedRoute === "safe"
    ? [
        { icon: "straight", text: "Head out from your starting point.", distance: "200 feet" },
        { icon: "right", text: "Turn right onto the green sidewalk route.", distance: "0.2 miles" },
        { icon: "straight", text: "Continue toward Jonesboro Road.", distance: "0.3 miles" },
        { icon: "left", text: "Turn left at the safer marked crossing.", distance: "250 feet" },
        { icon: "pin", text: `Arrive at ${destination || "your destination"}.`, distance: "Destination" }
      ]
    : [
        { icon: "straight", text: "Head out from your starting point.", distance: "200 feet" },
        { icon: "right", text: "Turn right onto the direct route.", distance: "0.2 miles" },
        { icon: "straight", text: "Continue near the high traffic crossing.", distance: "0.3 miles" },
        { icon: "left", text: "Turn left past the missing sidewalk segment.", distance: "400 feet" },
        { icon: "pin", text: `Arrive at ${destination || "your destination"}.`, distance: "Destination" }
      ];

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
        pickingLocation={reportOpen}
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
      {routeStatus === "done" && (
        <div className="directions-widget">
          <div className="directions-panel">
            <header>
              <strong>{selectedRoute === "safe" ? routeData.safe_route.duration_min : routeData.default_route.duration_min} min walking</strong>
              <span>{selectedRoute === "safe" ? routeData.safe_route.distance_mi : routeData.default_route.distance_mi} mi</span>
            </header>
            <ol>
              {routeSteps.map((step) => (
                <li key={step.text}>
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
        {reportOpen && (
          <ReportPanel
            pendingPin={pendingPin}
            photoPreview={photoPreview}
            hasPhoto={Boolean(photoFile)}
            note={note}
            setNote={setNote}
            onPhotoSelected={choosePhoto}
            status={reportStatus}
            message={reportMessage}
            onSubmit={submitReport}
          />
        )}
        <button className="report-fab" onClick={toggleReport}>
          {reportOpen ? "Close" : "Report gap"}
        </button>
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
