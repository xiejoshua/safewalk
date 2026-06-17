"""scripts/test_gemini_gap.py — smoke-test the Gemini gap-photo verification.

Runs the SAME code path the /analyze-gap endpoint uses (no DB write, no photo upload),
so you can confirm the Gemini key + request + JSON parsing all work end-to-end before
testing the full UI.

Usage (from the backend/ directory, with the venv active):
    python scripts/test_gemini_gap.py path/to/your/photo.jpg     # recommended
    python scripts/test_gemini_gap.py --submit path/to/photo.jpg # also writes a pin to Supabase
    python scripts/test_gemini_gap.py                            # no path: downloads a random
                                                                 #   image (connectivity test only;
                                                                 #   will almost certainly be REJECTED)

For a "VERIFIED" result, pass a real photo of a broken/missing sidewalk, an obstruction,
an unlit street, etc. A selfie, food, or screenshot should come back REJECTED — that's
the AI gate working correctly.
"""
from __future__ import annotations

import json
import mimetypes
import sys
from pathlib import Path

# Make `app` importable and load backend/.env regardless of where this is run from.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.gap_reports import (  # noqa: E402
    GapReportError,
    analyze_gap_photo,
    submit_verified_gap,
)

# Random placeholder image — only used when no path is given. It's not a sidewalk,
# so expect a REJECT; this branch just proves the key/request/parse pipeline works.
_FALLBACK_URL = "https://picsum.photos/800/600"

# A demo coordinate on the Gillem corridor, used only with --submit.
_DEMO_LNG, _DEMO_LAT = -84.3987, 33.6905


def load_image(path_arg: str | None) -> tuple[bytes, str]:
    if path_arg:
        path = Path(path_arg)
        if not path.exists():
            raise FileNotFoundError(f"No such file: {path}")
        media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return path.read_bytes(), media_type

    import httpx

    print(f"No image path given - downloading a random test image from {_FALLBACK_URL}")
    print("(This is only a connectivity test; a non-sidewalk image will be REJECTED.)\n")
    resp = httpx.get(_FALLBACK_URL, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "image/jpeg")


def main() -> int:
    args = sys.argv[1:]
    do_submit = "--submit" in args
    paths = [a for a in args if not a.startswith("--")]
    path_arg = paths[0] if paths else None

    try:
        image_bytes, media_type = load_image(path_arg)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not load image: {exc}")
        return 1

    print(f"Analyzing {len(image_bytes)} bytes ({media_type}) with Gemini...\n")

    try:
        if do_submit:
            print(f"--submit: will insert a pin at {_DEMO_LAT}, {_DEMO_LNG} if verified.\n")
            result = submit_verified_gap(
                image_bytes, media_type, _DEMO_LNG, _DEMO_LAT, gap_type="", user_note=""
            )
        else:
            result = analyze_gap_photo(image_bytes, media_type)
    except GapReportError as exc:
        print(f"Configuration error: {exc}")
        print("-> Check GEMINI_API_KEY (and SUPABASE_* if using --submit) in backend/.env")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Call failed: {exc}")
        return 1

    print(json.dumps(result, indent=2, default=str))
    print()

    if result.get("verified"):
        if do_submit and result.get("report"):
            print(f"[VERIFIED + STORED] pin id {result['report'].get('id')} "
                  f"(type {result['report'].get('type')}, confidence {result.get('confidence')})")
        else:
            print(f"[VERIFIED] type '{result.get('type')}' "
                  f"(confidence {result.get('confidence')}) - note: {result.get('note')}")
    else:
        print(f"[REJECTED] {result.get('reason')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
