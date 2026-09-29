"""Opt-in live smoke check for the configured Gemini and Imagen APIs.

Run explicitly with ``python test_gemini_features.py``; this is intentionally
not executed during test discovery because it consumes network/API quota.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not configured; skipping live API checks.")
        return 0

    from google import genai

    client = genai.Client(api_key=key)
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                'You are a Veo 3 cinematic prompt director. Expand: '
                '"Golden eagle soaring in mountains" into 1 cinematic prompt sentence.'
            ),
        )
        print("Gemini 2.5 Flash Response:")
        print(resp.text)
    except Exception as exc:
        print("Gemini text check failed:", exc)
        return 1

    try:
        img_resp = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=(
                "A majestic golden eagle soaring over snow covered mountain peaks "
                "at sunrise, 8k cinematic photography"
            ),
            config={"number_of_images": 1, "aspect_ratio": "16:9"},
        )
        print("Imagen image generation succeeded.")
        if img_resp.generated_images:
            print("Generated image bytes length:", len(img_resp.generated_images[0].image.image_bytes))
    except Exception as exc:
        print("Imagen check failed:", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
