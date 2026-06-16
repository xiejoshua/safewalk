"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import MapLibreGlDirections, { LoadingIndicatorControl, layersFactory } from "@maplibre/maplibre-gl-directions";
import maplibregl, {
  type DataDrivenPropertyValueSpecification,
  type LayerSpecification,
  type StyleSpecification
} from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { gapTypeMeta, type GapReport } from "../lib/gapReports";

const styleUrl = "https://tiles.openfreemap.org/styles/liberty";
const initialCenter: [number, number] = [-84.4194, 33.689];
export type RouteStatus = "idle" | "loading" | "error" | "done";
export type RouteChoice = "safe" | "default";
export type ThemeMode = "light" | "dark";

type RealMapProps = {
  destination: string;
  startCoords: [number, number] | null;
  destinationCoords: [number, number] | null;
  routeRequest: number;
  selectedRoute: RouteChoice;
  theme: ThemeMode;
  onRouteStatus: (status: RouteStatus) => void;
  gapReports: GapReport[];
  pickingLocation: boolean;
  pendingPin: [number, number] | null;
  onPickLocation: (coords: [number, number]) => void;
};

type SegmentWeights = {
  hazards: number;
  missingSidewalk: number;
  lowAccessibility: number;
  traffic: number;
};

type WeightedRouteSegment = {
  coordinates: [number, number][];
  weights: SegmentWeights;
  score: number;
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
    return !hasIcon && !hiddenLayers.includes(layer.id);
  });

  if (theme === "dark") {
    style.layers?.forEach(darkenBaseLayer);
  }

  return style as unknown as StyleSpecification;
}

function safetyScore(weights: SegmentWeights) {
  const risk =
    weights.hazards * 0.34 +
    weights.missingSidewalk * 0.28 +
    weights.lowAccessibility * 0.18 +
    weights.traffic * 0.2;

  return Math.max(0, Math.min(100, Math.round(100 - risk * 100)));
}

function demoWeightsForSegment(index: number, total: number, routeChoice: RouteChoice): SegmentWeights {
  const progress = total <= 1 ? 0 : index / (total - 1);

  if (routeChoice === "default") {
    return {
      hazards: Math.min(1, 0.25 + progress * 0.65),
      missingSidewalk: Math.min(1, 0.35 + progress * 0.55),
      lowAccessibility: 0.3 + progress * 0.35,
      traffic: Math.min(1, 0.45 + progress * 0.45)
    };
  }

  return {
    hazards: progress > 0.62 ? 0.55 : 0.12 + progress * 0.18,
    missingSidewalk: progress > 0.62 ? 0.5 : 0.08 + progress * 0.12,
    lowAccessibility: 0.12 + progress * 0.2,
    traffic: 0.18 + progress * 0.32
  };
}

function buildWeightedSegments(
  coordinates: [number, number][],
  routeChoice: RouteChoice,
  backendWeights?: SegmentWeights[]
) {
  return coordinates.slice(0, -1).map((coordinate, index) => {
    const weights = backendWeights?.[index] ?? demoWeightsForSegment(index, coordinates.length - 1, routeChoice);
    return {
      coordinates: [coordinate, coordinates[index + 1]],
      weights,
      score: safetyScore(weights)
    };
  });
}

async function fetchOsrmRoute(startPoint: [number, number], destinationPoint: [number, number]) {
  const coords = `${startPoint.join(",")};${destinationPoint.join(",")}`;
  const response = await fetch(
    `https://router.project-osrm.org/route/v1/foot/${coords}?overview=full&geometries=geojson`
  );
  if (!response.ok) throw new Error("Failed to fetch route geometry");
  const data = await response.json();
  const coordinates = data.routes?.[0]?.geometry?.coordinates as [number, number][] | undefined;
  if (!coordinates?.length) throw new Error("Missing route geometry");

  return coordinates;
}

function createWeightedRouteData(segments: WeightedRouteSegment[]) {
  return {
    type: "FeatureCollection" as const,
    features: segments.map((segment) => ({
      type: "Feature" as const,
      properties: {
        score: segment.score,
        hazards: segment.weights.hazards,
        missingSidewalk: segment.weights.missingSidewalk,
        lowAccessibility: segment.weights.lowAccessibility,
        traffic: segment.weights.traffic
      },
      geometry: {
        type: "LineString" as const,
        coordinates: segment.coordinates
      }
    }))
  };
}

function drawWeightedRoute(map: maplibregl.Map, segments: WeightedRouteSegment[], theme: ThemeMode) {
  const route = createWeightedRouteData(segments);
  const source = map.getSource("safewalk-gradient-route") as maplibregl.GeoJSONSource | undefined;

  if (source) {
    source.setData(route);
    return;
  }

  map.addSource("safewalk-gradient-route", {
    type: "geojson",
    data: route
  });

  map.addLayer({
    id: "safewalk-gradient-route-line",
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
  const meta = gapTypeMeta(report.type);
  const note = report.note ? `<p style="margin:4px 0 0;font-size:12px;color:#444;">${escapeHtml(report.note)}</p>` : "";
  const photo = report.photo_url
    ? `<img src="${escapeHtml(report.photo_url)}" alt="${escapeHtml(meta.label)}" style="width:100%;border-radius:8px;margin-top:6px;display:block;" />`
    : "";
  const when = report.reported_at
    ? `<small style="color:#888;">${new Date(report.reported_at).toLocaleString()}</small>`
    : "";
  return `<div style="max-width:220px;font-family:inherit;">
      <strong style="color:${meta.color};font-size:13px;">${escapeHtml(meta.label)}</strong>
      ${note}${photo}
      <div style="margin-top:6px;">${when}</div>
    </div>`;
}

export default function RealMap({
  destination,
  startCoords,
  destinationCoords,
  routeRequest,
  selectedRoute,
  theme,
  onRouteStatus,
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
        setupDirections(map);
        setMapReady(true);
      });

      // Drop-a-pin: when the report flow is picking a location, a map click
      // chooses where the new gap is.
      map.on("click", (event) => {
        if (!pickingRef.current) return;
        onPickRef.current?.([event.lngLat.lng, event.lngLat.lat]);
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
    if (!map?.isStyleLoaded()) return;

    if (map.getLayer("safewalk-gradient-route-line")) {
      map.setPaintProperty("safewalk-gradient-route-line", "line-color", routeColorScale(theme));
    }
  }, [selectedRoute, theme]);

  useEffect(() => {
    if (!routeRequest || !directionsRef.current || !mapRef.current) return;
    if (!startCoords || !destinationCoords) {
      onRouteStatus("error");
      return;
    }

    const startPoint = startCoords;
    const destinationPoint = destinationCoords;
    onRouteStatus("loading");

    directionsRef.current
      .setWaypoints([startPoint, destinationPoint])
      .then(async () => {
        const routeCoordinates = await fetchOsrmRoute(startPoint, destinationPoint);
        const weightedSegments = buildWeightedSegments(routeCoordinates, selectedRoute);
        if (mapRef.current) drawWeightedRoute(mapRef.current, weightedSegments, theme);
        const bounds = new maplibregl.LngLatBounds();
        bounds.extend(startPoint);
        bounds.extend(destinationPoint);
        mapRef.current?.fitBounds(bounds, { padding: 90, duration: 850 });
        onRouteStatus("done");
      })
      .catch(() => onRouteStatus("error"));
  }, [destinationCoords, onRouteStatus, routeRequest, selectedRoute, startCoords, theme]);

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
      const meta = gapTypeMeta(report.type);
      const popup = new maplibregl.Popup({ offset: 18, closeButton: true }).setHTML(
        gapPopupHtml(report)
      );
      return new maplibregl.Marker({ element: createGapPinElement(meta.color) })
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
