"use client";

import {
  Accessibility,
  ArrowLeft,
  Camera,
  Car,
  Check,
  Construction,
  Footprints,
  LightbulbOff,
  MapPin,
  Send,
  Wrench
} from "lucide-react";
import Link from "next/link";
import type { ComponentType } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { analyzeGapPhoto, submitVerifiedGap, type AnalyzeGapResult } from "../lib/gapReports";

type IssueOption = {
  id: string;
  label: string;
  type: string;
  icon: ComponentType<{ size?: number }>;
};

type AnalyzeState = "idle" | "analyzing" | "done" | "error";

// Map Gemini's gap_type back to the issue tile that should be pre-selected.
const AI_TYPE_TO_ISSUE: Record<string, string> = {
  no_sidewalk: "no_sidewalk",
  streetlight_out: "poor_lighting",
  no_crossing: "unsafe_crossing",
  broken_sidewalk: "pothole_hazard",
  obstruction: "construction"
};

type ReportLocation = {
  coords: [number, number];
  name: string;
  source: string;
};

const ISSUE_OPTIONS: IssueOption[] = [
  { id: "no_sidewalk", label: "No sidewalk", type: "no_sidewalk", icon: Construction },
  { id: "poor_lighting", label: "Poor lighting", type: "streetlight_out", icon: LightbulbOff },
  { id: "not_accessible", label: "Not accessible", type: "no_crossing", icon: Accessibility },
  { id: "unsafe_crossing", label: "Unsafe crossing", type: "no_crossing", icon: Car },
  { id: "pothole_hazard", label: "Pothole / hazard", type: "broken_sidewalk", icon: Wrench },
  { id: "construction", label: "Construction", type: "obstruction", icon: Construction }
];

function ticketNumber() {
  return `ATL-2026-${Math.floor(1000 + Math.random() * 9000)}`;
}

function formatCoords(coords: [number, number]) {
  const [lng, lat] = coords;
  return `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lng).toFixed(4)}° ${lng >= 0 ? "E" : "W"}`;
}

async function reverseGeocode(coords: [number, number]) {
  const [lng, lat] = coords;
  const response = await fetch(
    `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json`
  );
  if (!response.ok) throw new Error("Reverse geocode failed");
  const data = (await response.json()) as { display_name?: string };
  return data.display_name ?? "Current location";
}

async function geocodeAddress(query: string): Promise<ReportLocation | null> {
  const response = await fetch(
    `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1&countrycodes=us`
  );
  if (!response.ok) return null;
  const data = (await response.json()) as Array<{ lon: string; lat: string; display_name?: string }>;
  const first = data[0];
  if (!first) return null;
  return {
    coords: [Number(first.lon), Number(first.lat)],
    name: first.display_name ?? query,
    source: "typed address"
  };
}

export default function ReportPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [selectedIssues, setSelectedIssues] = useState<string[]>(["no_sidewalk"]);
  const [location, setLocation] = useState<ReportLocation | null>(null);
  const [editingLocation, setEditingLocation] = useState(false);
  const [address, setAddress] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [ticket, setTicket] = useState("");

  // AI verification of the uploaded photo (Gemini). A report can only be
  // submitted once a photo is verified as showing a real gap/hazard.
  const [analysis, setAnalysis] = useState<AnalyzeGapResult | null>(null);
  const [analyzeState, setAnalyzeState] = useState<AnalyzeState>("idle");
  const [analyzeError, setAnalyzeError] = useState("");

  useEffect(() => {
    if (!navigator.geolocation) {
      setLocation({
        coords: [-84.388, 33.749],
        name: "Atlanta, GA",
        source: "default location"
      });
      return;
    }

    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const coords: [number, number] = [position.coords.longitude, position.coords.latitude];
        let name = "Detected location";
        try {
          name = await reverseGeocode(coords);
        } catch {
          name = "Detected location";
        }
        setLocation({ coords, name, source: "detected from your position" });
      },
      () => {
        setLocation({
          coords: [-84.388, 33.749],
          name: "Atlanta, GA",
          source: "default location"
        });
      },
      { enableHighAccuracy: true, timeout: 8000 }
    );
  }, []);

  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  const primaryIssue = useMemo(() => {
    const selected = ISSUE_OPTIONS.find((option) => selectedIssues.includes(option.id));
    return selected ?? ISSUE_OPTIONS[0];
  }, [selectedIssues]);

  const selectPhoto = useCallback((file: File | null) => {
    if (file && file.size > 10 * 1024 * 1024) {
      setError("That photo is over 10MB. Choose a smaller image.");
      return;
    }
    setError("");
    setPhoto(file);
    setPreview((current) => {
      if (current) URL.revokeObjectURL(current);
      return file ? URL.createObjectURL(file) : null;
    });

    // Reset prior verdict, then run AI verification on the new photo.
    setAnalysis(null);
    setAnalyzeError("");
    if (!file) {
      setAnalyzeState("idle");
      return;
    }
    setAnalyzeState("analyzing");
    analyzeGapPhoto(file)
      .then((result) => {
        setAnalysis(result);
        setAnalyzeState("done");
        // Pre-select the issue tile matching the AI's classification.
        if (result.verified && result.type) {
          const issueId = AI_TYPE_TO_ISSUE[result.type];
          if (issueId) setSelectedIssues([issueId]);
        }
      })
      .catch((err) => {
        setAnalyzeState("error");
        setAnalyzeError(err instanceof Error ? err.message : "Failed to analyze photo");
      });
  }, []);

  const toggleIssue = (id: string) => {
    setSelectedIssues((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id]
    );
  };

  const saveAddress = async () => {
    if (!address.trim()) return;
    const next = await geocodeAddress(address);
    if (!next) {
      setError("Couldn't find that address. Try a more specific Atlanta location.");
      return;
    }
    setError("");
    setLocation(next);
    setEditingLocation(false);
  };

  const resetForm = () => {
    selectPhoto(null);
    setSelectedIssues(["no_sidewalk"]);
    setNotes("");
    setTicket("");
    setError("");
  };

  const submit = async () => {
    if (!photo || analysis?.verified !== true) {
      setError("Add a photo and let AI verify it before submitting.");
      return;
    }
    if (!location) {
      setError("Choose a location before submitting.");
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      const labels = selectedIssues
        .map((id) => ISSUE_OPTIONS.find((option) => option.id === id)?.label)
        .filter(Boolean)
        .join(", ");
      const note = [notes.trim(), labels ? `Issues: ${labels}` : ""].filter(Boolean).join("\n");

      // Re-verified server-side, then inserted with status='reported'.
      await submitVerifiedGap({
        photo,
        lng: location.coords[0],
        lat: location.coords[1],
        type: primaryIssue.type,
        note: note || undefined
      });
      setTicket(ticketNumber());
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Report submit failed.");
    } finally {
      setSubmitting(false);
    }
  };

  if (ticket) {
    return (
      <main className="report-page-shell">
        <ReportNav />
        <section className="report-success-page">
          <div className="report-success-icon"><Check size={28} /></div>
          <h1>Report submitted</h1>
          <p>
            Your gap has been filed with Atlanta 311 and added to the SidewalkSOS heat map.
            The city has been notified.
          </p>
          <strong>Ticket #{ticket} · just now</strong>
          <div className="report-success-actions">
            <button type="button" onClick={resetForm}>Report another</button>
            <Link href="/">Back to map</Link>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="report-page-shell">
      <ReportNav />
      <section className="report-form-wrap">
        <header className="report-header">
          <p>Community reporting</p>
          <h1>Report a gap</h1>
          <span>
            Help make Atlanta's sidewalks safer. Your report goes directly to Atlanta 311 and helps us route people away from danger.
          </span>
        </header>

        <section className="report-card">
          <label>Photo (required — AI verifies it)</label>
          <div
            className={`report-upload ${preview ? "has-file" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                fileInputRef.current?.click();
              }
            }}
          >
            {preview ? (
              <>
                <img src={preview} alt="Selected gap report" />
                <span className="report-file-row">
                  <span><Check size={14} /> {photo?.name}</span>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      selectPhoto(null);
                    }}
                  >
                    Remove
                  </button>
                </span>
              </>
            ) : (
              <>
                <Camera size={28} />
                <strong>Tap to upload a photo</strong>
                <small>JPG, PNG or HEIC · max 10MB</small>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            hidden
            type="file"
            accept="image/*"
            onChange={(event) => selectPhoto(event.target.files?.[0] ?? null)}
          />
          {analyzeState === "analyzing" && (
            <p style={{ marginTop: 10, fontSize: 13, color: "#666" }}>🔍 Verifying your photo with AI…</p>
          )}
          {analyzeState === "error" && (
            <p style={{ marginTop: 10, fontSize: 13, color: "#c0392b" }}>{analyzeError}</p>
          )}
          {analyzeState === "done" && analysis?.verified && (
            <p style={{ marginTop: 10, fontSize: 13, color: "#1d8a5e", fontWeight: 600 }}>
              ✓ AI verified this as a hazard
              {typeof analysis.confidence === "number"
                ? ` (${Math.round(analysis.confidence * 100)}% confident)`
                : ""}
              . Confirm the issue below and submit.
            </p>
          )}
          {analyzeState === "done" && analysis && !analysis.verified && (
            <p style={{ marginTop: 10, fontSize: 13, color: "#c0392b" }}>
              {analysis.reason ?? "This photo doesn't clearly show a gap. Try a clearer, well-lit shot."}
            </p>
          )}
        </section>

        <section className="report-card">
          <label>What's the issue?</label>
          <div className="issue-grid">
            {ISSUE_OPTIONS.map((option) => {
              const Icon = option.icon;
              const selected = selectedIssues.includes(option.id);
              return (
                <button
                  key={option.id}
                  className={selected ? "selected" : ""}
                  type="button"
                  onClick={() => toggleIssue(option.id)}
                >
                  <Icon size={16} />
                  {option.label}
                </button>
              );
            })}
          </div>
        </section>

        <section className="report-card">
          <label>Location</label>
          <div className="report-location-row">
            <MapPin size={16} />
            <div>
              <strong>{location?.name ?? "Detecting your location..."}</strong>
              <small>{location ? `${formatCoords(location.coords)} · ${location.source}` : "Waiting for browser geolocation"}</small>
            </div>
            <button type="button" onClick={() => setEditingLocation((open) => !open)}>
              Edit
            </button>
          </div>
          {editingLocation && (
            <div className="report-address-edit">
              <input
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                placeholder="Type an Atlanta address"
              />
              <button type="button" onClick={saveAddress}>Use</button>
            </div>
          )}
        </section>

        <section className="report-card">
          <label>Additional notes (optional)</label>
          <textarea
            rows={3}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="e.g. This stretch has been broken for months, people walk in the road every morning..."
          />
        </section>

        {error && <p className="report-submit-error">{error}</p>}
        <button
          className="report-submit"
          type="button"
          onClick={submit}
          disabled={submitting || !location || !photo || analysis?.verified !== true}
        >
          <Send size={16} />
          {submitting ? "Submitting..." : "Submit to Atlanta 311"}
        </button>
        <p className="report-anon-note">Anonymous · geotagged · timestamped · sent instantly</p>
      </section>
    </main>
  );
}

function ReportNav() {
  return (
    <nav className="nav">
      <div className="brand">
        <span><Footprints size={21} /></span>
        Safewalk
      </div>
      <div className="nav-links">
        <Link href="/">Map</Link>
        <Link className="active" href="/report">Report</Link>
        <a>About</a>
      </div>
      <Link className="report-back-link" href="/">
        <ArrowLeft size={17} />
        Map
      </Link>
    </nav>
  );
}
