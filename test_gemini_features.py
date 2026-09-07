import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables safely
load_dotenv(Path(__file__).parent / ".env")
load_dotenv()

key = os.environ.get("GEMINI_API_KEY")
print(f"GEMINI_API_KEY configured: {bool(key)}")

from google import genai

client = genai.Client(api_key=key)

try:
    resp = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='You are a Veo 3 cinematic prompt director. Expand: "Golden eagle soaring in mountains" into 1 cinematic prompt sentence.'
    )
    print("Gemini 2.5 Flash Response:")
    print(resp.text)
except Exception as e:
    print("Gemini text failed:", e)

# Test Image generation if supported
try:
    img_resp = client.models.generate_images(
        model='imagen-3.0-generate-002',
        prompt='A majestic golden eagle soaring over snow covered mountain peaks at sunrise, 8k cinematic photography',
        config=dict(
            number_of_images=1,
            aspect_ratio='16:9',
        )
    )
    print("Imagen 3 image generation succeeded!")
    if img_resp.generated_images:
        print("Generated image bytes length:", len(img_resp.generated_images[0].image.image_bytes))
except Exception as e:
    print("Imagen 3 test note:", e)
