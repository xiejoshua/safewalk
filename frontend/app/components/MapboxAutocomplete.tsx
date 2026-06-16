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
};

export default function MapboxAutocomplete({
  value,
  onChange,
  onSelect
}: MapboxAutocompleteProps) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    const query = value.trim();

    if (!token || query.length < 2) {
      setSuggestions([]);
      return;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(async () => {
      setLoading(true);
      try {
        const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(
          query
        )}.json?access_token=${token}&autocomplete=true&types=address,poi&proximity=-84.3880,33.7490`;
        const res = await fetch(url, { signal: controller.signal });
        const data = (await res.json()) as { features?: Suggestion[] };
        setSuggestions(data.features ?? []);
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
        onFocus={() => suggestions.length > 0 && setOpen(true)}
        placeholder="Enter destination..."
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
                  onChange(suggestion.place_name);
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
