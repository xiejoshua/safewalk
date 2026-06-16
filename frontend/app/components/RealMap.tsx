"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import MapLibreGlDirections, { LoadingIndicatorControl, layersFactory } from "@maplibre/maplibre-gl-directions";
import maplibregl, {
  type DataDrivenPropertyValueSpecification,
  type LayerSpecification,
  type StyleSpecification
} from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { gapTypeMeta, statusMeta, type GapReport } from "../lib/gapReports";

const styleUrl = "https://tiles.openfreemap.org/styles/liberty";
const initialCenter: [number, number] = [-84.4194, 33.689];
const sidewalkSourceId = "sidewalks";
const sidewalkLayerId = "sidewalk-layer";
const routeLayerId = "safewalk-gradient-route-line";
export type RouteStatus = "idle" | "loading" | "error" | "noroute" | "done";
export type RouteChoice = "safe" | "default";
export type ThemeMode = "light" | "dark";

type RealMapProps = {
  destination: string;
  startCoords: [number, number] | null;
  destinationCoords: [number, number] | null;
  routeRequest: number;
  selectedRoute: RouteChoice;
  theme: ThemeMode;
  sidewalkVisible: boolean;
  onSidewalkLayerAvailable: (available: boolean) => void;
  onRouteStatus: (status: RouteStatus) => void;
  routeFeatures: GeoJSON.FeatureCollection | null;
  gapReports: GapReport[];
  pickingLocation: boolean;
  pendingPin: [number, number] | null;
  onPickLocation: (coords: [number, number]) => void;
};

const hiddenLayers = [
  "park",
  "park_outline",
  "landuse_residential",
  "landuse_pitch",
  "landuse_track",
  "landuse_cemetery",
  "landuse_hospital",
  "landuse_school",
  "aeroway_fill",
  "aeroway_runway",
  "aeroway_taxiway",
  "road_one_way_arrow",
  "road_one_way_arrow_opposite",
  "building",
  "building-3d",
  "waterway_line_label",
  "water_name_point_label",
  "water_name_line_label",
  "poi_r20",
  "poi_r7",
  "poi_r1",
  "poi_transit",
  "airport",
  "label_other",
  "label_village",
  "label_town",
  "label_state",
  "label_city",
  "label_city_capital",
  "label_country_3",
  "label_country_2",
  "label_country_1",
  "highway-shield-non-us",
  "highway-shield-us-interstate",
  "road_shield_us"
];

type LooseMapStyle = {
  sprite?: string;
  layers?: Array<{
    id: string;
    type?: string;
    layout?: Record<string, unknown>;
    paint?: Record<string, unknown>;
  }>;
  [key: string]: unknown;
};

function routeColorScale(theme: ThemeMode) {
  return [
    "interpolate",
    ["linear"],
    ["get", "score"],
    0,
    theme === "dark" ? "#ff5f52" : "#c0392b",
    40,
    theme === "dark" ? "#ff9a3d" : "#e76f2e",
    65,
    theme === "dark" ? "#ffd75a" : "#e8c547",
    85,
    theme === "dark" ? "#48e5a3" : "#2d7a5e"
  ] as unknown as DataDrivenPropertyValueSpecification<string>;
}

function darkenBaseLayer(layer: NonNullable<LooseMapStyle["layers"]>[number]) {
  layer.paint = layer.paint ?? {};

  if (layer.type === "background") {
    layer.paint["background-color"] = "#101614";
  }

  if (layer.type === "fill") {
    layer.paint["fill-color"] = layer.id.includes("water") ? "#0d2025" : "#151c18";
    layer.paint["fill-opacity"] = 1;
  }

  if (layer.type === "line") {
    layer.paint["line-color"] = layer.id.includes("road") ? "#3c4942" : "#26322d";
    layer.paint["line-opacity"] = layer.id.includes("road") ? 0.82 : 0.45;
  }

  if (layer.type === "symbol") {
    layer.paint["text-color"] = "#a9b4ab";
    layer.paint["text-halo-color"] = "#101614";
    layer.paint["text-halo-width"] = 1.1;
  }
}

async function loadRoadOnlyStyle(theme: ThemeMode) {
  const res = await fetch(styleUrl);
  const style = (await res.json()) as LooseMapStyle;

  delete style.sprite;
  style.layers = style.layers?.filter((layer) => {
    const hasIcon = Boolean(layer.layout?.["icon-image"]);
    const hasPattern = Object.keys(layer.paint ?? {}).some((key) => key.includes("pattern"));
    return !hasIcon && !hasPattern && !hiddenLayers.includes(layer.id);
  });

  if (theme === "dark") {
    style.layers?.forEach(darkenBaseLayer);
  }

  return style as unknown as StyleSpecification;
}

// Draw the backend's safety-scored route as a gradient line. The FeatureCollection
// comes straight from GET /route (each segment carries a `score` 0=unsafe..100=safe).
function drawRouteFeatures(
  map: maplibregl.Map,
  features: GeoJSON.FeatureCollection,
  theme: ThemeMode
) {
  const source = map.getSource("safewalk-gradient-route") as maplibregl.GeoJSONSource | undefined;

  if (source) {
    source.setData(features);
    return;
  }

  map.addSource("safewalk-gradient-route", {
    type: "geojson",
    data: features
  });

  map.addLayer({
    id: routeLayerId,
    type: "line",
    source: "safewalk-gradient-route",
    paint: {
      "line-color": routeColorScale(theme),
      "line-width": 7,
      "line-opacity": 0.96
    },
    layout: {
      "line-cap": "round",
      "line-join": "round"
    }
  });
}

function syncSidewalkLayerStyle(map: maplibregl.Map) {
  if (!map.getLayer(sidewalkLayerId)) return;

  const zoom = map.getZoom();
  const width = zoom >= 14 ? 2.5 : zoom >= 12 ? 1.5 : zoom >= 10 ? 0.8 : 0;
  const opacity = zoom >= 14 ? 0.7 : zoom >= 12 ? 0.45 : zoom >= 10 ? 0.25 : 0;

  map.setPaintProperty(sidewalkLayerId, "line-width", width);
  map.setPaintProperty(sidewalkLayerId, "line-opacity", opacity);
}

function ensureSidewalkBelowRoute(map: maplibregl.Map) {
  if (!map.getLayer(sidewalkLayerId) || !map.getLayer(routeLayerId)) return;
  map.moveLayer(sidewalkLayerId, routeLayerId);
}

function directionLayers(routeChoice: RouteChoice) {
  return layersFactory().map((layer) => {
    if (layer.id === "maplibre-gl-directions-routeline") {
      return {
        ...layer,
        paint: {
          ...layer.paint,
          "line-width": 9,
          "line-opacity": 0
        }
      };
    }

    if (layer.id === "maplibre-gl-directions-alt-routeline") {
      return {
        ...layer,
        paint: {
          ...layer.paint,
          "line-color": "#aaa69d",
          "line-width": 4,
          "line-opacity": 0
        }
      };
    }

    return layer;
  }) as LayerSpecification[];
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function createGapPinElement(color: string, pending = false) {
  const el = document.createElement("div");
  el.style.width = pending ? "20px" : "16px";
  el.style.height = pending ? "20px" : "16px";
  el.style.borderRadius = "50% 50% 50% 0";
  el.style.transform = "rotate(-45deg)";
  el.style.background = pending ? "#1D9E75" : color;
  el.style.border = "2px solid #fff";
  el.style.boxShadow = "0 1px 4px rgba(0,0,0,0.4)";
  el.style.cursor = "pointer";
  if (pending) {
    el.style.animation = "safewalk-pin-pulse 1.2s ease-in-out infinite";
  }
  return el;
}

function gapPopupHtml(report: GapReport) {
  const type = gapTypeMeta(report.type);
  const status = statusMeta(report.status);
  const note = report.note ? `<p style="margin:4px 0 0;font-size:12px;color:#444;">${escapeHtml(report.note)}</p>` : "";
  const photo = report.photo_url
    ? `<img src="${escapeHtml(report.photo_url)}" alt="${escapeHtml(type.label)}" style="width:100%;border-radius:8px;margin-top:6px;display:block;" />`
    : "";
  const when = report.reported_at
    ? `<small style="color:#888;">${new Date(report.reported_at).toLocaleString()}</small>`
    : "";
  const badge = `<span style="display:inline-block;margin-top:4px;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;color:#fff;background:${status.color};">${escapeHtml(status.label)}</span>`;
  return `<div style="max-width:220px;font-family:inherit;">
      <strong style="font-size:13px;">${escapeHtml(type.label)}</strong><br/>
      ${badge}
      ${note}${photo}
      <div style="margin-top:6px;">${when}</div>
    </div>`;
}

async function fetchSidewalks() {
  const response = await fetch("/api/sidewalks/");
  if (!response.ok) return null;

  const data = (await response.json()) as GeoJSON.FeatureCollection;
  const hasLineFeatures = data.features?.some((feature) => feature.geometry?.type === "LineString");
  return hasLineFeatures ? data : null;
}

export default function RealMap({
  destination,
  startCoords,
  destinationCoords,
  routeRequest,
  selectedRoute,
  theme,
  sidewalkVisible,
  onSidewalkLayerAvailable,
  onRouteStatus,
  routeFeatures,
  gapReports,
  pickingLocation,
  pendingPin,
  onPickLocation
}: RealMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const directionsRef = useRef<MapLibreGlDirections | null>(null);
  const loadingControlRef = useRef<LoadingIndicatorControl | null>(null);
  const gapMarkersRef = useRef<maplibregl.Marker[]>([]);
  const pendingMarkerRef = useRef<maplibregl.Marker | null>(null);
  const pickingRef = useRef(pickingLocation);
  const onPickRef = useRef(onPickLocation);
  const [mapReady, setMapReady] = useState(false);
  void destination;

  // Keep the click handler reading the latest picking state / callback.
  pickingRef.current = pickingLocation;
  onPickRef.current = onPickLocation;

  const destroyDirections = () => {
    if (!directionsRef.current) return;

    try {
      directionsRef.current.destroy();
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      if (!message.includes("Cannot remove non-existing layer")) {
        throw error;
      }
    } finally {
      directionsRef.current = null;
    }
  };

  const setupDirections = (map: maplibregl.Map) => {
    destroyDirections();
    directionsRef.current = new MapLibreGlDirections(map, {
      api: "https://router.project-osrm.org/route/v1",
      profile: "foot",
      requestOptions: {
        alternatives: "true",
        overview: "full",
        geometries: "geojson"
      },
      layers: directionLayers(selectedRoute)
    });
    directionsRef.current.interactive = true;
    directionsRef.current.setWaypoints(startCoords ? [startCoords] : []);

    if (!loadingControlRef.current) {
      loadingControlRef.current = new LoadingIndicatorControl(directionsRef.current);
      map.addControl(loadingControlRef.current, "bottom-right");
    }
  };

  const setupSidewalkLayer = async (map: maplibregl.Map) => {
    try {
      if (map.getLayer(sidewalkLayerId)) {
        map.setLayoutProperty(sidewalkLayerId, "visibility", sidewalkVisible ? "visible" : "none");
        syncSidewalkLayerStyle(map);
        ensureSidewalkBelowRoute(map);
        onSidewalkLayerAvailable(true);
        return;
      }

      const data = await fetchSidewalks();
      if (!data) {
        onSidewalkLayerAvailable(false);
        return;
      }

      if (!map.getSource(sidewalkSourceId)) {
        map.addSource(sidewalkSourceId, {
          type: "geojson",
          data
        });
      }

      map.addLayer({
        id: sidewalkLayerId,
        type: "line",
        source: sidewalkSourceId,
        layout: {
          "line-join": "round",
          "line-cap": "butt",
          visibility: sidewalkVisible ? "visible" : "none"
        },
        paint: {
          "line-color": "#2f8f2f",
          "line-dasharray": [10, 5],
          "line-width": 2.5,
          "line-opacity": 0.7
        }
      });
      syncSidewalkLayerStyle(map);
      ensureSidewalkBelowRoute(map);
      onSidewalkLayerAvailable(true);
    } catch {
      onSidewalkLayerAvailable(false);
    }
  };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cancelled = false;

    async function initMap() {
      const style = await loadRoadOnlyStyle(theme);
      if (!containerRef.current || cancelled) return;

      const map = new maplibregl.Map({
        container: containerRef.current,
        style,
        center: startCoords ?? initialCenter,
        zoom: 13.8,
        minZoom: 10,
        maxZoom: 18,
        attributionControl: false
      });

      map.scrollZoom.setWheelZoomRate(1 / 450);
      map.scrollZoom.setZoomRate(1 / 140);
      map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
      mapRef.current = map;

      map.on("load", () => {
        void setupSidewalkLayer(map);
        setupDirections(map);
        setMapReady(true);
      });

      // Drop-a-pin: when the report flow is picking a location, a map click
      // chooses where the new gap is.
      map.on("click", (event) => {
        if (!pickingRef.current) return;
        onPickRef.current?.([event.lngLat.lng, event.lngLat.lat]);
      });

      map.on("zoomend", () => {
        syncSidewalkLayerStyle(map);
      });
    }

    initMap();

    return () => {
      cancelled = true;
      destroyDirections();
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const currentMap = map;

    let cancelled = false;

    async function updateStyle() {
      const style = await loadRoadOnlyStyle(theme);
      if (cancelled) return;

      destroyDirections();
      currentMap.setStyle(style);
      currentMap.once("idle", () => {
        if (cancelled) return;
        void setupSidewalkLayer(currentMap);
        setupDirections(currentMap);
      });
    }

    updateStyle();

    return () => {
      cancelled = true;
    };
  }, [theme]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer(sidewalkLayerId)) return;

    map.setLayoutProperty(sidewalkLayerId, "visibility", sidewalkVisible ? "visible" : "none");
  }, [sidewalkVisible]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;

    if (map.getLayer(routeLayerId)) {
      map.setPaintProperty(routeLayerId, "line-color", routeColorScale(theme));
    }
  }, [selectedRoute, theme]);

  // Draw the safety-scored route from the backend (page owns fetch + status).
  // routeFeatures changes whenever a new route comes back; we render its geometry
  // and place the start/end waypoint markers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !routeFeatures || !startCoords || !destinationCoords) return;

    // Place start/end markers (MapLibreGlDirections computes its own line invisibly).
    directionsRef.current?.setWaypoints([startCoords, destinationCoords]).catch(() => {});

    drawRouteFeatures(map, routeFeatures, theme);
    ensureSidewalkBelowRoute(map);

    const bounds = new maplibregl.LngLatBounds();
    bounds.extend(startCoords);
    bounds.extend(destinationCoords);
    map.fitBounds(bounds, { padding: 90, duration: 850 });
  }, [routeFeatures, startCoords, destinationCoords, theme]);

  // Crosshair cursor while the user is choosing a location for a new report.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.getCanvas().style.cursor = pickingLocation ? "crosshair" : "";
  }, [pickingLocation, mapReady]);

  // Render a pin for every existing/live gap report. Markers are DOM overlays, so
  // they survive theme restyles and update in place when the report list changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    gapMarkersRef.current.forEach((marker) => marker.remove());
    gapMarkersRef.current = gapReports.map((report) => {
      const color = statusMeta(report.status).color;
      const popup = new maplibregl.Popup({ offset: 18, closeButton: true }).setHTML(
        gapPopupHtml(report)
      );
      return new maplibregl.Marker({ element: createGapPinElement(color) })
        .setLngLat([report.lng, report.lat])
        .setPopup(popup)
        .addTo(map);
    });

    return () => {
      gapMarkersRef.current.forEach((marker) => marker.remove());
      gapMarkersRef.current = [];
    };
  }, [gapReports, mapReady]);

  // Show a pulsing marker at the location the user picked for the report they're filing.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    pendingMarkerRef.current?.remove();
    pendingMarkerRef.current = null;

    if (pendingPin) {
      pendingMarkerRef.current = new maplibregl.Marker({
        element: createGapPinElement("#1D9E75", true)
      })
        .setLngLat(pendingPin)
        .addTo(map);
    }
  }, [pendingPin, mapReady]);

  return <div ref={containerRef} className="real-map" />;
}
