"use client";

import { useEffect, useRef, useState } from "react";

type Suggestion = {
  id: string;
  place_name: string;
  center: [number, number];
};

type MapboxAutocompleteProps = {
  value: string;
  onChange: (value: string) => void;
  onSelect: (coords: [number, number], placeName: string) => void;
  placeholder?: string;
};

const georgiaBbox = "-85.6052,30.3579,-80.8397,35.0007";

const georgiaPlaces: Suggestion[] = [
  { id: "ga-gillem-logistics", place_name: "Gillem Logistics Center, Forest Park, GA", center: [-84.33703, 33.61649] },
  { id: "ga-fountain-school", place_name: "Fountain School, Forest Park, GA", center: [-84.37381, 33.61178] },
  { id: "ga-starr-park", place_name: "Starr Park, Forest Park, GA", center: [-84.36659, 33.61761] },
  { id: "ga-georgia-tech", place_name: "Georgia Tech, Atlanta, GA", center: [-84.3963, 33.7756] },
  { id: "ga-piedmont-park", place_name: "Piedmont Park, Atlanta, GA", center: [-84.3733, 33.7851] },
  { id: "ga-beltline", place_name: "Atlanta BeltLine Eastside Trail, Atlanta, GA", center: [-84.3648, 33.7668] },
  { id: "ga-centennial", place_name: "Centennial Olympic Park, Atlanta, GA", center: [-84.393, 33.7603] },
  { id: "ga-decatur", place_name: "Decatur Square, Decatur, GA", center: [-84.2963, 33.7748] }
];

function localGeorgiaSuggestions(query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return [];

  return georgiaPlaces.filter((place) => place.place_name.toLowerCase().includes(normalized));
}

function dedupeSuggestions(suggestions: Suggestion[]) {
  const seen = new Set<string>();
  return suggestions.filter((suggestion) => {
    const key = suggestion.place_name.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export default function MapboxAutocomplete({
  value,
  onChange,
  onSelect,
  placeholder = "Enter destination..."
}: MapboxAutocompleteProps) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    const query = value.trim();

    if (query.length === 0) {
      setSuggestions([]);
      setOpen(false);
      return;
    }

    if (query.length < 2) {
      setSuggestions(localGeorgiaSuggestions(query));
      setOpen(true);
      return;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(async () => {
      setLoading(true);
      try {
        const localSuggestions = localGeorgiaSuggestions(query);

        if (token) {
          const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(
            query
          )}.json?access_token=${token}&autocomplete=true&country=us&bbox=${georgiaBbox}&types=address,poi,place,locality,neighborhood&proximity=-84.3880,33.7490`;
          const res = await fetch(url, { signal: controller.signal });
          const data = (await res.json()) as { features?: Suggestion[] };
          setSuggestions(dedupeSuggestions([...localSuggestions, ...(data.features ?? [])]).slice(0, 8));
        } else {
          const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(
            `${query}, Georgia`
          )}&format=json&limit=8&countrycodes=us&viewbox=${georgiaBbox}&bounded=1`;
          const res = await fetch(url, { signal: controller.signal });
          const data = (await res.json()) as Array<{
            place_id: number;
            display_name: string;
            lon: string;
            lat: string;
          }>;
          setSuggestions(
            dedupeSuggestions([
              ...localSuggestions,
              ...data.map((item) => ({
                id: String(item.place_id),
                place_name: item.display_name,
                center: [Number(item.lon), Number(item.lat)] as [number, number]
              }))
            ]).slice(0, 8)
          );
        }
        setOpen(true);
      } catch {
        if (!controller.signal.aborted) setSuggestions([]);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [value]);

  useEffect(() => {
    function closeOnOutsideClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }

    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, []);

  return (
    <div className="relative w-full" ref={containerRef}>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => {
          if (value.trim()) setOpen(true);
        }}
        placeholder={placeholder}
        className="h-11 w-full rounded-lg border border-[#ddd8cf] bg-white px-4 text-[22px] text-[#1a1a1a] outline-none placeholder:text-[#aaa69d]"
      />
      {open && (
        <div className="absolute left-0 right-0 top-[50px] z-30 overflow-hidden rounded-lg border border-[#e8e4dc] bg-white shadow-lg">
          {loading && <div className="px-4 py-3 text-sm text-[#8a8680]">Searching...</div>}
          {!loading &&
            suggestions.map((suggestion) => (
              <button
                key={suggestion.id}
                type="button"
                onClick={() => {
                  onSelect(suggestion.center, suggestion.place_name);
                  setOpen(false);
                }}
                className="block w-full px-4 py-3 text-left text-sm text-[#1a1a1a] hover:bg-[#e8f5ef]"
              >
                {suggestion.place_name}
              </button>
            ))}
          {!loading && suggestions.length === 0 && (
            <div className="px-4 py-3 text-sm text-[#8a8680]">No results</div>
          )}
        </div>
      )}
    </div>
  );
}
