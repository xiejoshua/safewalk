export const USE_MOCK = true;

export type Mode = "walk" | "accessible" | "night";
export type GapType =
  | "no_sidewalk"
  | "accessibility"
  | "unsafe_crossing"
  | "pothole"
  | "construction";

export const routeData = {
  safe_route: {
    duration_min: 22,
    distance_mi: 1.1,
    safety_score: 78,
    sidewalk_status: "Full sidewalk",
    geometry: [
      [33.689, -84.4194],
      [33.692, -84.415],
      [33.6955, -84.411],
      [33.699, -84.4075],
      [33.702, -84.404]
    ] as [number, number][]
  },
  default_route: {
    duration_min: 16,
    distance_mi: 0.8,
    danger_zones: 2,
    missing_sidewalk_mi: 0.4,
    geometry: [
      [33.689, -84.4194],
      [33.69, -84.416],
      [33.692, -84.412],
      [33.6955, -84.408],
      [33.702, -84.404]
    ] as [number, number][],
    danger_markers: [
      { lat: 33.692, lng: -84.416, type: "no_sidewalk" },
      { lat: 33.6955, lng: -84.412, type: "no_sidewalk" }
    ]
  }
};

export const scoreData = {
  overall: 78,
  sidewalk: 90,
  traffic_speed: 65,
  crash_history: 82,
  accessible: 70
};

export const gapReports = [
  {
    lat: 33.692,
    lng: -84.416,
    location: "Jonesboro Rd at I-20 corridor",
    type: "No sidewalk",
    reported: "12 min ago",
    status: "Sent to 311"
  },
  {
    lat: 33.702,
    lng: -84.404,
    location: "Southside Works entrance",
    type: "Unsafe crossing",
    reported: "Today",
    status: "Under review"
  },
  {
    lat: 33.689,
    lng: -84.4194,
    location: "Gillem Station connector",
    type: "Accessibility",
    reported: "Yesterday",
    status: "Sent to 311"
  }
];
