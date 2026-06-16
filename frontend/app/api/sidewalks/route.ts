import { NextResponse } from "next/server";

const sidewalkServiceUrl =
  "https://services2.arcgis.com/zLeajbicrDRLQcny/ArcGIS/rest/services/Sidewalks_Inventory/FeatureServer/2/query";
const martaAreaEnvelope = "-84.52,33.61,-84.20,33.97";
const pageSize = 2000;

type SidewalkProperties = {
  OBJECTID?: number;
  SW_ID?: number;
  StreetName?: string;
  SidewalkType?: string;
  ObservedCondition?: string;
  SWCIRating?: string;
  quality?: "full" | "partial" | "none";
};

function qualityFromRating(properties: SidewalkProperties) {
  const rating = properties.SWCIRating?.toLowerCase() ?? "";
  const type = properties.SidewalkType?.toLowerCase() ?? "";
  const condition = properties.ObservedCondition?.toLowerCase() ?? "";

  if (rating.includes("no sidewalk") || type.includes("no sw") || condition.includes("no sw")) {
    return "none";
  }

  if (rating.includes("excellent") || rating.includes("good")) return "full";
  if (rating.includes("fair") || rating.includes("poor")) return "partial";
  return "full";
}

function normalizeFeature(feature: GeoJSON.Feature): GeoJSON.Feature {
  const properties = (feature.properties ?? {}) as SidewalkProperties;

  return {
    ...feature,
    properties: {
      ...properties,
      quality: qualityFromRating(properties)
    }
  };
}

export async function GET() {
  const baseParams = {
    f: "geojson",
    where: "1=1",
    outFields: "OBJECTID,SW_ID,StreetName,SidewalkType,ObservedCondition,SWCIRating",
    returnGeometry: "true",
    outSR: "4326",
    geometry: martaAreaEnvelope,
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    resultRecordCount: String(pageSize)
  };

  try {
    const features: GeoJSON.Feature[] = [];

    for (let offset = 0; ; offset += pageSize) {
      const params = new URLSearchParams({
        ...baseParams,
        resultOffset: String(offset)
      });

      const response = await fetch(`${sidewalkServiceUrl}?${params}`, {
        signal: AbortSignal.timeout(12000),
        cache: "no-store"
      });

      if (!response.ok) throw new Error("Sidewalk service request failed");

      const data = (await response.json()) as GeoJSON.FeatureCollection;
      const pageFeatures = (data.features ?? []).map(normalizeFeature);
      features.push(
        ...pageFeatures.filter((feature) => feature.geometry?.type === "LineString" && feature.properties?.quality !== "none")
      );

      if (pageFeatures.length < pageSize) break;
    }

    return NextResponse.json({
      type: "FeatureCollection",
      features
    });
  } catch {
    return NextResponse.json({ type: "FeatureCollection", features: [] });
  }
}
