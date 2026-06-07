from groq import Groq
from app.config import settings
import yt_dlp, json, re

client = Groq(api_key=settings.GROQ_API_KEY)

def get_video_info(url: str) -> dict:
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", ""),
                "description": info.get("description", ""),
                "uploader": info.get("uploader", ""),
                "tags": info.get("tags", []),
            }
    except Exception as e:
        raise Exception(f"Could not fetch video metadata from {url}: {str(e)}")

def generate_metadata(url: str) -> dict:
    video_info = get_video_info(url)

    prompt = f"""
You are a viral TikTok content strategist. Based on the original video info below, generate optimized metadata to maximize reach and virality on TikTok.

Original Title: {video_info['title']}
Original Description: {video_info['description'][:500]}
Original Tags: {', '.join(video_info['tags'][:10]) if video_info['tags'] else 'none'}

Respond ONLY with a valid JSON object, no markdown, no extra text:
{{
  "title": "Short punchy TikTok title under 60 chars with a hook",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7"],
  "caption": "TikTok caption with hook opener, value, and CTA. Include 3-5 relevant hashtags inline."
}}

Rules:
- Title must start with a hook (number, question, or bold statement)
- Tags: mix of trending broad tags + niche specific tags, no # symbol
- Caption: conversational, 150-200 chars, end with a call to action
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # ✅ valid Groq model
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if Groq wraps response in ```json ... ```
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        if not raw:
            raise Exception("Groq returned an empty response.")

        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise Exception(f"Groq returned invalid JSON: {str(e)}. Raw response: {raw[:200]}")
    except Exception as e:
        raise Exception(f"Groq metadata generation failed: {str(e)}")