"""gap_reports.py — live crowdsourced gap-report pipeline.

Two-step flow used by the /status report form:
    1. /analyze-gap : photo bytes
         -> Gemini vision decides whether a real pedestrian-access gap is visible
         -> returns {verified, type, note, confidence} WITHOUT writing anything.
            The frontend uses this to pre-select the category and unlock Submit.
    2. /submit-gap : photo bytes + coordinates + user-chosen type + note
         -> re-verifies with Gemini (the gate can't be bypassed by posting straight
            to /submit-gap), then uploads the photo to Supabase Storage and inserts
            the row with the user's chosen category and status='reported'.
         -> the realtime publication pushes the new pin to every subscribed map.

The AI verification is the gate: an unverified photo never becomes a pin. Because
the insert runs server-side here (not from the browser), the gate can't be bypassed
by posting straight to PostgREST.

All third-party clients are created lazily and cached so importing this module never
requires credentials (keeps the rest of the app importable offline / in tests).
"""
from __future__ import annotations

import logging
import uuid
from functools import lru_cache

from pydantic import BaseModel
from shapely import wkb as shapely_wkb
from shapely.geometry import Point

from app.config import get_settings

logger = logging.getLogger(__name__)

# Hazard vocabulary — must match the gap_reports.type CHECK constraint in schema.sql
# and the weights in layers/hazards.py.
GAP_TYPES = (
    "broken_sidewalk",
    "no_sidewalk",
    "no_crossing",
    "obstruction",
    "streetlight_out",
    "other",
)

# Minimum model confidence to accept a report. Below this we reject and ask for a
# clearer photo rather than dropping a low-quality pin onto the map.
_MIN_CONFIDENCE = 0.55

_SYSTEM_PROMPT = (
    "You verify crowdsourced sidewalk-hazard reports for a pedestrian safety map. "
    "You are shown a photo a pedestrian took of a suspected gap in walking "
    "infrastructure. Decide whether the photo genuinely shows a pedestrian-access "
    "hazard, and if so, classify it.\n\n"
    "Set is_gap=true ONLY when a real, visible problem is present:\n"
    "  broken_sidewalk — cracked, heaved, crumbling, or obstructed-by-damage pavement\n"
    "  no_sidewalk     — a walking route where the sidewalk is missing or ends abruptly\n"
    "  no_crossing     — a missing crosswalk or missing ADA curb ramp at a crossing\n"
    "  obstruction     — pole, vegetation, parked car, or debris blocking the path\n"
    "  streetlight_out — an unlit / dark pedestrian stretch or a broken streetlight\n"
    "  other           — a clear pedestrian hazard that fits none of the above\n\n"
    "Set is_gap=false when the photo shows an intact sidewalk, an unrelated subject "
    "(a selfie, food, indoors, a screenshot), or is too blurry/dark to judge. "
    "note: one short factual sentence describing what is visible (shown on the map pin). "
    "confidence: 0.0-1.0, how sure you are this is a real, classifiable hazard."
)

class GapReportError(RuntimeError):
    """Configuration or upstream failure while processing a gap report."""


class _GapVerdict(BaseModel):
    """Structured output Gemini must return for each analyzed photo."""

    is_gap: bool
    type: str
    note: str
    confidence: float


@lru_cache
def _gemini_client():
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GapReportError(
            "GEMINI_API_KEY is not set — photo verification is unavailable."
        )
    from google import genai

    return genai.Client(api_key=settings.gemini_api_key)


@lru_cache
def _supabase_client():
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        raise GapReportError(
            "SUPABASE_URL / SUPABASE_KEY are not set — cannot store gap reports."
        )
    from supabase import create_client

    return create_client(settings.supabase_url, settings.supabase_service_key)


def _analyze_with_gemini(image_bytes: bytes, media_type: str) -> dict:
    """Ask Gemini whether the photo shows a real gap; returns the parsed JSON verdict.

    Uses Gemini structured output (response_schema) so the reply is always valid
    JSON matching _GapVerdict. Falls back to parsing response.text if needed.
    """
    import json

    from google.genai import types

    settings = get_settings()
    client = _gemini_client()
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=media_type),
            "Is this a real pedestrian-access gap? Classify it.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=_GapVerdict,
            temperature=0.0,
        ),
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, _GapVerdict):
        return parsed.model_dump()
    # Fallback: structured parse unavailable — read the raw JSON text.
    return json.loads(response.text or "{}")


def _upload_photo(image_bytes: bytes, media_type: str) -> str:
    """Upload the verified photo to the public gap-photos bucket; return its URL."""
    settings = get_settings()
    bucket = settings.gap_photos_bucket
    ext = {"image/png": "png", "image/webp": "webp", "image/jpeg": "jpg"}.get(
        media_type, "jpg"
    )
    path = f"{uuid.uuid4().hex}.{ext}"

    sb = _supabase_client()
    sb.storage.from_(bucket).upload(
        path,
        image_bytes,
        {"content-type": media_type, "upsert": "false"},
    )
    return sb.storage.from_(bucket).get_public_url(path)


def _insert_report(
    lng: float, lat: float, gap_type: str, note: str, photo_url: str | None
) -> dict:
    """Insert one verified report; return the created row (incl. generated lng/lat)."""
    # Hex EWKB is parsed natively by the PostGIS geography input function, so it
    # inserts through PostgREST without an explicit ST_GeogFromText cast.
    geom_hex = shapely_wkb.dumps(Point(lng, lat), hex=True, srid=4326)

    sb = _supabase_client()
    rows = (
        sb.table("gap_reports")
        .insert(
            {
                "geom": geom_hex,
                "type": gap_type,
                "note": note,
                "photo_url": photo_url,
                "status": "reported",
            }
        )
        .execute()
        .data
    )
    return rows[0] if rows else {}


def create_gap_report(
    lng: float, lat: float, note: str = "", gap_type: str = "other"
) -> dict:
    """Create a crowdsourced report without photo verification."""
    normalized_type = gap_type if gap_type in GAP_TYPES else "other"
    return _insert_report(lng, lat, normalized_type, note.strip(), None)


def list_gap_reports() -> list[dict]:
    """Return all gap reports for the map (newest first).

    Lets the frontend load existing pins through the backend instead of querying
    Supabase directly. Rows without coordinates (shouldn't happen post-migration)
    are dropped so the map never receives an unplottable pin.
    """
    sb = _supabase_client()
    rows = (
        sb.table("gap_reports")
        .select("id,type,note,photo_url,lng,lat,status,reported_at")
        .order("reported_at", desc=True)
        .execute()
        .data
    )
    return [
        row
        for row in (rows or [])
        if row.get("lng") is not None and row.get("lat") is not None
    ]


# Status workflow values — keep in sync with migration 0002's CHECK constraint.
GAP_STATUSES = ("reported", "in_progress", "processed")


def update_gap_report_status(report_id: str, status: str) -> dict:
    """Update one report's status; returns the updated row (empty dict if not found)."""
    sb = _supabase_client()
    rows = (
        sb.table("gap_reports")
        .update({"status": status})
        .eq("id", report_id)
        .execute()
        .data
    )
    return rows[0] if rows else {}


_ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/webp"}


def _normalize_media_type(media_type: str) -> str:
    return media_type if media_type in _ALLOWED_MEDIA else "image/jpeg"


def analyze_gap_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """Verify a photo shows a real gap WITHOUT storing anything (the /analyze-gap step).

    Returns a dict the route serializes directly:
      rejected -> {verified: False, reason, ai_type, confidence}
      accepted -> {verified: True, type, note, confidence}
    The frontend uses `type` to pre-select the category radio and unlock Submit.
    """
    if not image_bytes:
        raise GapReportError("No image data received.")

    media_type = _normalize_media_type(media_type)
    verdict = _analyze_with_gemini(image_bytes, media_type)

    is_gap = bool(verdict.get("is_gap"))
    confidence = float(verdict.get("confidence") or 0.0)
    ai_type = verdict.get("type") if verdict.get("type") in GAP_TYPES else "other"
    ai_note = (verdict.get("note") or "").strip()

    if not is_gap or confidence < _MIN_CONFIDENCE:
        logger.info(
            "gap photo rejected (is_gap=%s, confidence=%.2f)", is_gap, confidence
        )
        return {
            "verified": False,
            "reason": (
                "The photo doesn't clearly show a sidewalk gap or hazard. "
                "Try a clearer, well-lit shot of the problem."
            ),
            "ai_type": ai_type,
            "confidence": round(confidence, 2),
        }

    return {
        "verified": True,
        "type": ai_type,
        "note": ai_note,
        "confidence": round(confidence, 2),
    }


def submit_verified_gap(
    image_bytes: bytes,
    media_type: str,
    lng: float,
    lat: float,
    gap_type: str = "",
    user_note: str = "",
) -> dict:
    """Re-verify a photo and, if real, persist it as a live pin (the /submit-gap step).

    Re-running the AI check keeps the gate intact even though /analyze-gap ran first:
    a client can't bypass verification by POSTing straight to /submit-gap.

    Returns a dict the route serializes directly:
      rejected -> {verified: False, reason, ai_type, confidence}
      accepted -> {verified: True, report:{id,type,note,photo_url,lng,lat,...}, confidence}
    """
    analysis = analyze_gap_photo(image_bytes, media_type)
    if not analysis["verified"]:
        return analysis

    media_type = _normalize_media_type(media_type)
    # Prefer the user's chosen category; fall back to the AI's classification.
    chosen_type = gap_type if gap_type in GAP_TYPES else analysis["type"]
    # Prefer the user's note when supplied; otherwise use the AI's description.
    note = user_note.strip() or analysis["note"]
    photo_url = _upload_photo(image_bytes, media_type)
    report = _insert_report(lng, lat, chosen_type, note, photo_url)

    logger.info("gap_report stored: %s (%s)", report.get("id"), chosen_type)
    return {"verified": True, "report": report, "confidence": analysis["confidence"]}


def verify_and_record_gap(
    image_bytes: bytes,
    media_type: str,
    lng: float,
    lat: float,
    user_note: str = "",
) -> dict:
    """Back-compat verify + store using the AI's own classification (no user category)."""
    return submit_verified_gap(image_bytes, media_type, lng, lat, "", user_note)
