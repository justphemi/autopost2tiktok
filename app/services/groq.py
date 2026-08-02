"""
Groq metadata generation for YouTube uploads.

Calls llama-3.1-8b-instant (default) with response_format=json_object to
produce {title, description, tags} for a source video link + short
description. Falls back to regex extraction if the model wraps JSON in
code fences, and finally to a raw-text return so the UI can show the
model output for the user to edit.
"""
import json
import re
import httpx

from app.config import settings


SYSTEM_PROMPT = (
    "You are a YouTube SEO expert. Given a source video link and a brief "
    "description, produce JSON with three fields: "
    '"title" (string, <= 100 characters, attention-grabbing and SEO-friendly), '
    '"description" (string, <= 5000 characters, opens with a 2-3 sentence hook, '
    'then 5-8 inline hashtags, then 1-2 sentences of context), and '
    '"tags" (array of 8-15 short keyword strings). '
    "Respond with ONLY valid JSON, no prose, no markdown code fences."
)


def _extract_json(text: str):
    """Best-effort JSON extraction: try direct parse, then strip code fences
    and grab the first {...} block. Returns the parsed dict, or None."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    cleaned = text.strip()
    # Strip leading ```json or ``` fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    # Find the first balanced {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def generate_metadata(link: str, description: str) -> dict:
    """
    Returns one of:
      {"title": str, "description": str, "tags": list[str]}
      {"raw": True, "text": str}  # model did not return parseable JSON
    """
    if not settings.GROQ_API_KEY:
        return {"raw": True, "text": "GROQ_API_KEY is not configured on the server."}

    user_prompt = (
        f"Source link: {link}\n"
        f"Short description: {description.strip() or '(none provided)'}\n\n"
        "Generate the YouTube metadata."
    )

    try:
        with httpx.Client(timeout=30) as client:
            res = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.GROQ_MODEL,
                    "temperature": 0.4,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
    except httpx.TimeoutException:
        return {"raw": True, "text": "Groq request timed out. Try again in a moment."}
    except httpx.RequestError as e:
        return {"raw": True, "text": f"Network error contacting Groq: {e}"}

    if res.status_code >= 400:
        return {"raw": True, "text": f"Groq error {res.status_code}: {res.text[:300]}"}

    try:
        body = res.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        return {"raw": True, "text": f"Malformed Groq response: {e}"}

    parsed = _extract_json(content)
    if parsed is None:
        return {"raw": True, "text": content}

    # Validate / sanitize shape. We accept whatever the model gave us and
    # truncate to platform limits as a safety net.
    title = str(parsed.get("title", "")).strip()[:100]
    desc_text = str(parsed.get("description", "")).strip()[:5000]
    raw_tags = parsed.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [str(t).strip() for t in raw_tags if str(t).strip()][:15]

    if not title or not desc_text:
        return {"raw": True, "text": content}

    return {"title": title, "description": desc_text, "tags": tags}
