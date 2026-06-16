"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  fetchGapReports,
  gapTypeMeta,
  STATUS_META,
  STATUS_ORDER,
  statusMeta,
  updateGapStatus,
  type GapReport,
  type GapStatus
} from "../lib/gapReports";

export default function StatusPage() {
  const [reports, setReports] = useState<GapReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setReports(await fetchGapReports());
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const changeStatus = useCallback(
    async (id: string, status: GapStatus) => {
      setBusyId(id);
      setError("");
      try {
        const updated = await updateGapStatus(id, status);
        setReports((current) => current.map((r) => (r.id === id ? { ...r, ...updated } : r)));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to update status");
      } finally {
        setBusyId(null);
      }
    },
    []
  );

  return (
    <main style={{ maxWidth: 880, margin: "0 auto", padding: "32px 20px", fontFamily: "inherit" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 style={{ fontSize: 24, margin: 0 }}>Gap report status</h1>
        <Link href="/" style={{ fontSize: 14, color: "#1d9e75" }}>← Back to map</Link>
      </header>

      {error && <p style={{ color: "#c0392b", fontSize: 13 }}>{error}</p>}

      {loading ? (
        <p style={{ color: "#888" }}>Loading reports…</p>
      ) : reports.length === 0 ? (
        <p style={{ color: "#888" }}>No gap reports yet.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          {reports.map((report) => {
            const type = gapTypeMeta(report.type);
            const status = statusMeta(report.status);
            return (
              <li
                key={report.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 16,
                  padding: "12px 16px",
                  border: "1px solid #e5e5df",
                  borderRadius: 12,
                  background: "#fff"
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <strong style={{ fontSize: 14 }}>{type.label}</strong>
                    <span
                      style={{
                        padding: "1px 8px",
                        borderRadius: 999,
                        fontSize: 11,
                        fontWeight: 600,
                        color: "#fff",
                        background: status.color
                      }}
                    >
                      {status.label}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: "#666", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 420 }}>
                    {report.note || "—"}
                  </div>
                  <div style={{ fontSize: 11, color: "#aaa", marginTop: 2 }}>
                    {report.reported_at ? new Date(report.reported_at).toLocaleString() : ""}
                  </div>
                </div>

                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  {STATUS_ORDER.map((key) => {
                    const active = report.status === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        disabled={active || busyId === report.id}
                        onClick={() => changeStatus(report.id, key)}
                        title={`Mark ${STATUS_META[key].label}`}
                        style={{
                          padding: "5px 10px",
                          borderRadius: 8,
                          fontSize: 12,
                          fontWeight: 600,
                          cursor: active ? "default" : "pointer",
                          border: `1px solid ${STATUS_META[key].color}`,
                          background: active ? STATUS_META[key].color : "transparent",
                          color: active ? "#fff" : STATUS_META[key].color,
                          opacity: busyId === report.id && !active ? 0.5 : 1
                        }}
                      >
                        {STATUS_META[key].label}
                      </button>
                    );
                  })}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
