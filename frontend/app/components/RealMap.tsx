"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import maplibregl, { type DataDrivenPropertyValueSpecification, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";

const styleUrl = "https://tiles.openfreemap.org/styles/liberty";
const gillem: [number, number] = [-84.4194, 33.689];
const southAtlantaStart: [number, number] = [-84.4058, 33.7042];

export type RouteStatus = "idle" | "loading" | "error" | "done";
export type RouteChoice = "safe" | "default";
export type ThemeMode = "light" | "dark";

type LineGeometry = {
  type: "LineString";
  coordinates: [number, number][];
};

type RealMapProps = {
  destination: string;
  routeRequest: number;
  selectedRoute: RouteChoice;
  theme: ThemeMode;
  onRouteStatus: (status: RouteStatus) => void;
};

const demoRoutes = {
  safe: [
  { score: 82, coordinates: [southAtlantaStart, [-84.407, 33.7018]] },
  { score: 68, coordinates: [[-84.407, 33.7018], [-84.4095, 33.6988]] },
  { score: 49, coordinates: [[-84.4095, 33.6988], [-84.4124, 33.6961]] },
  { score: 31, coordinates: [[-84.4124, 33.6961], [-84.4156, 33.6928]] },
  { score: 74, coordinates: [[-84.4156, 33.6928], gillem] }
  ],
  default: [
    { score: 72, coordinates: [southAtlantaStart, [-84.4076, 33.7012]] },
    { score: 55, coordinates: [[-84.4076, 33.7012], [-84.4102, 33.6994]] },
    { score: 28, coordinates: [[-84.4102, 33.6994], [-84.4134, 33.6967]] },
    { score: 22, coordinates: [[-84.4134, 33.6967], [-84.4163, 33.6934]] },
    { score: 38, coordinates: [[-84.4163, 33.6934], gillem] }
  ]
} as const;

const demo311Reports = [
  { label: "Construction", coordinates: [-84.4095, 33.6988] },
  { label: "Rough road", coordinates: [-84.4124, 33.6961] },
  { label: "Sidewalk hazard", coordinates: [-84.4156, 33.6928] }
] as const;

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

function routeFeature(geometry: LineGeometry, score = 78) {
  return {
    type: "Feature" as const,
    properties: { score },
    geometry
  };
}

function createLabel(text: string, className = "") {
  const element = document.createElement("div");
  element.className = `map-label ${className}`;
  element.textContent = text;
  return element;
}

function createWarningMarker(label: string) {
  const element = document.createElement("div");
  element.className = "map-warning-marker";
  element.title = label;
  element.setAttribute("aria-label", label);
  element.innerHTML = "<span>!</span>";
  return element;
}

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

function createRouteData(routeChoice: RouteChoice) {
  return {
    type: "FeatureCollection" as const,
    features: demoRoutes[routeChoice].map((segment) =>
      routeFeature({
        type: "LineString",
        coordinates: segment.coordinates.map(([lng, lat]) => [lng, lat])
      }, segment.score)
    )
  };
}

function fitDemoRoute(map: maplibregl.Map, routeChoice: RouteChoice, duration = 400) {
  const bounds = new maplibregl.LngLatBounds();
  demoRoutes[routeChoice].forEach((segment) => {
    segment.coordinates.forEach(([lng, lat]) => bounds.extend([lng, lat]));
  });
  map.fitBounds(bounds, { padding: 110, duration });
}

function addDemoRoute(map: maplibregl.Map, routeChoice: RouteChoice, theme: ThemeMode) {
  map.addSource("demo-safety-route", {
    type: "geojson",
    data: createRouteData(routeChoice)
  });

  map.addLayer({
    id: "demo-safety-route-line",
    type: "line",
    source: "demo-safety-route",
    paint: {
      "line-color": routeColorScale(theme),
      "line-width": theme === "dark" ? 7 : 6,
      "line-opacity": 0.95
    },
    layout: {
      "line-cap": "round",
      "line-join": "round"
    }
  });

  fitDemoRoute(map, routeChoice, 0);
}

export default function RealMap({ destination, routeRequest, selectedRoute, theme, onRouteStatus }: RealMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  void destination;
  void routeRequest;
  void onRouteStatus;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cancelled = false;

    async function initMap() {
      const style = await loadRoadOnlyStyle(theme);
      if (!containerRef.current || cancelled) return;

      const map = new maplibregl.Map({
        container: containerRef.current,
        style,
        center: gillem,
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
        addDemoRoute(map, selectedRoute, theme);

        demo311Reports.forEach((report) => {
          new maplibregl.Marker({
            element: createWarningMarker(report.label),
            anchor: "center"
          })
            .setLngLat([report.coordinates[0], report.coordinates[1]])
            .addTo(map);
        });

        new maplibregl.Marker({
          element: createLabel("South Atlanta", "dest-label"),
          anchor: "center"
        })
          .setLngLat(southAtlantaStart)
          .addTo(map);

        new maplibregl.Marker({
          element: createLabel("Gillem Station", "origin-label"),
          anchor: "center"
        })
          .setLngLat(gillem)
          .addTo(map);
      });
    }

    initMap();

    return () => {
      cancelled = true;
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

      currentMap.setStyle(style);
      currentMap.once("idle", () => {
        if (cancelled || currentMap.getSource("demo-safety-route")) return;
        addDemoRoute(currentMap, selectedRoute, theme);
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

    const source = map.getSource("demo-safety-route") as maplibregl.GeoJSONSource | undefined;
    source?.setData(createRouteData(selectedRoute));
    if (map.getLayer("demo-safety-route-line")) {
      map.setPaintProperty("demo-safety-route-line", "line-color", routeColorScale(theme));
      map.setPaintProperty("demo-safety-route-line", "line-width", theme === "dark" ? 7 : 6);
    }
    fitDemoRoute(map, selectedRoute);
  }, [selectedRoute, theme]);

  return <div ref={containerRef} className="real-map" />;
}
