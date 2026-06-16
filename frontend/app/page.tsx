"use client";

import {
  AlertTriangle,
  ArrowRight,
  Check,
  Clock3,
  Flag,
  Footprints,
  MapPin,
  Shield,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useMemo, useRef, useState } from "react";
import MapboxAutocomplete from "./components/MapboxAutocomplete";
import type { RouteChoice, RouteStatus } from "./components/RealMap";
import { scoreRoute, submitGapReport } from "./lib/backendApi";
import { routeData, scoreData } from "./lib/data";

type PreferenceKey = "sidewalk" | "traffic" | "accessibility";
const DOTS = 5;

const RealMap = dynamic(() => import("./components/RealMap"), { ssr: false });

const reportTypes = [
  "No sidewalk",
  "Not accessible",
  "Unsafe crossing",
  "Pothole/hazard",
  "Construction"
];

export default function Home() {
  const [tab, setTab] = useState<"routes" | "score">("routes");
  const [destination, setDestination] = useState("");
  const [destinationCoords, setDestinationCoords] = useState<[number, number] | null>(null);
  const [routeRequest, setRouteRequest] = useState(0);
  const [routeStatus, setRouteStatus] = useState<RouteStatus>("idle");
  const [selectedRoute, setSelectedRoute] = useState<RouteChoice>("safe");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const requestRoute = useCallback(async () => {
    if (!destination.trim()) return;
    setRouteStatus("loading");

    try {
      await scoreRoute({
        origin: [-84.4194, 33.689],
        dest: destinationCoords ?? [-84.4058, 33.7042],
        profile: "day"
      });
      setRouteStatus("done");
    } catch {
      setRouteStatus("error");
    }

    setRouteRequest((request) => request + 1);
  }, [destination, destinationCoords]);

  const co2 = useMemo(
    () => Math.round(routeData.safe_route.distance_mi * 1.1 * 10) / 10,
    []
  );

  return (
    <main className={`app-shell ${theme === "dark" ? "dark-mode" : ""}`}>
      <Nav theme={theme} onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))} />
      <section className="workspace">
        <aside className="sidebar">
          <div className="sidebar-card">
            <div className="search-box">
              <label className="field">
                <MapPin size={20} />
                <input value="Gillem MARTA Station" readOnly />
              </label>
              <label className="field muted">
                <Flag size={20} />
                <MapboxAutocomplete
                  value={destination}
                  onChange={setDestination}
                  onSelect={(coords, placeName) => {
                    setDestinationCoords(coords);
                    setDestination(placeName);
                  }}
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
            <PreferencePanel />
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
              <RoutesPanel co2={co2} selectedRoute={selectedRoute} onSelectRoute={setSelectedRoute} />
            )}
            {tab === "score" && <ScorePanel />}
          </div>
        </aside>

        <MapPanel
          destination={destination}
          destinationCoords={destinationCoords}
          routeRequest={routeRequest}
          selectedRoute={selectedRoute}
          theme={theme}
          onRouteStatus={setRouteStatus}
        />
      </section>
    </main>
  );
}

function PreferencePanel() {
  const [preferences, setPreferences] = useState<Record<PreferenceKey, number>>({
    sidewalk: 3,
    traffic: 2,
    accessibility: 2
  });

  const controls: [PreferenceKey, string][] = [
    ["sidewalk", "Sidewalk presence"],
    ["traffic", "Traffic exposure"],
    ["accessibility", "Accessibility"]
  ];

  return (
    <section className="preference-panel">
      {controls.map(([key, label]) => (
        <div className="preference-control" key={key}>
          <SnapSlider
            label={label}
            value={preferences[key]}
            onChange={(value) =>
              setPreferences((current) => ({
                ...current,
                [key]: value
              }))
            }
          />
        </div>
      ))}
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
        <a>Map</a>
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

function RoutesPanel({
  co2,
  selectedRoute,
  onSelectRoute
}: {
  co2: number;
  selectedRoute: RouteChoice;
  onSelectRoute: (route: RouteChoice) => void;
}) {
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
            <span><Clock3 size={15} /> Time: {routeData.safe_route.duration_min} min</span>
            <span>Distance: {routeData.safe_route.distance_mi} mi</span>
            <span className="ok"><Check size={15} /> Full sidewalk</span>
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
            <span>{routeData.default_route.danger_zones} danger zones</span>
          </header>
          <div className="stats-row">
            <span><Clock3 size={15} /> Time: {routeData.default_route.duration_min} min</span>
            <span>Distance: {routeData.default_route.distance_mi} mi</span>
            <span className="bad">x {routeData.default_route.missing_sidewalk_mi} mi no sidewalk</span>
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
        <button className="walk-route-btn" type="button">
          I walked this route
        </button>
      </div>
    </div>
  );
}

function ScorePanel() {
  const rows = [
    ["Sidewalk", scoreData.sidewalk],
    ["Traffic speed", scoreData.traffic_speed],
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

function ReportPanel({
  selected,
  setSelected,
  ticket,
  submit
}: {
  selected: string[];
  setSelected: (items: string[]) => void;
  ticket: string;
  submit: () => void;
}) {
  return (
    <div className="panel report-panel">
      <p>Tap a segment on the map, then choose what is wrong:</p>
      <div className="report-grid">
        {reportTypes.map((item) => (
          <button
            key={item}
            className={selected.includes(item) ? "picked" : ""}
            onClick={() =>
              setSelected(
                selected.includes(item)
                  ? selected.filter((x) => x !== item)
                  : [...selected, item]
              )
            }
          >
            {item}
          </button>
        ))}
      </div>
      <button className="primary-btn" onClick={submit}>
        {ticket || "Submit to Atlanta 311"} <ArrowRight size={18} />
      </button>
      <small>Anonymous · geotagged · timestamped</small>
    </div>
  );
}

function MapPanel({
  destination,
  destinationCoords,
  routeRequest,
  selectedRoute,
  theme,
  onRouteStatus
}: {
  destination: string;
  destinationCoords: [number, number] | null;
  routeRequest: number;
  selectedRoute: RouteChoice;
  theme: "light" | "dark";
  onRouteStatus: (status: RouteStatus) => void;
}) {
  const [reportOpen, setReportOpen] = useState(false);
  const [selectedReports, setSelectedReports] = useState<string[]>([]);
  const [ticket, setTicket] = useState("");
  const submitReport = useCallback(async () => {
    const report = await submitGapReport({
      coordinates: [-84.4124, 33.6961],
      type: selectedReports[0] ?? "Pothole/hazard"
    });
    setTicket(report.id);
  }, [selectedReports]);

  void destinationCoords;

  return (
    <section className="map-panel">
      <RealMap
        destination={destination}
        routeRequest={routeRequest}
        selectedRoute={selectedRoute}
        theme={theme}
        onRouteStatus={onRouteStatus}
      />
      <div className="legend">
        <span><i className="score-green" /> Safer</span>
        <span><i className="score-yellow" /> Caution</span>
        <span><i className="score-orange" /> Risky</span>
        <span><i className="score-red" /> Unsafe</span>
      </div>
      <div className="floating-report">
        {reportOpen && (
          <ReportPanel
            selected={selectedReports}
            setSelected={setSelectedReports}
            ticket={ticket}
            submit={submitReport}
          />
        )}
        <button className="report-fab" onClick={() => setReportOpen((open) => !open)}>
          Report gap
        </button>
      </div>
    </section>
  );
}
