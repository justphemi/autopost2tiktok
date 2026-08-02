"""
Direct-post service: downloads an already-rendered MP4 from a public URL
(typically a Supabase storage URL) and uploads it to TikTok as a draft.

This bypasses yt-dlp / ffmpeg / Groq entirely — it only relies on the
TikTok OAuth tokens you already have in `tiktok_accounts`.
"""
import os
import httpx
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.database import supabase
from app.services.tiktok import post_video  # reuse the existing init + upload flow


def _resolve_access_token(tiktok_open_id: str) -> str:
    """Pull the access_token for the given TikTok account from Supabase."""
    res = (
        supabase.table("tiktok_accounts")
        .select("access_token")
        .eq("tiktok_open_id", tiktok_open_id)
        .single()
        .execute()
    )
    if not res.data or not res.data.get("access_token"):
        raise Exception("TikTok account not connected or token missing. Reconnect in Settings.")
    return res.data["access_token"]


def _download_to_temp(video_url: str) -> str:
    """Stream a video file from a public URL to a local temp file. Returns the path."""
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        with client.stream("GET", video_url) as r:
            r.raise_for_status()

            # Prefer the server-suggested extension, fall back to .mp4
            ctype = r.headers.get("content-type", "")
            ext = ".mp4"
            if "video/" in ctype:
                subtype = ctype.split("/", 1)[1].split(";")[0].strip()
                if subtype:
                    ext = f".{subtype}"

            tmp = tempfile.NamedTemporaryFile(prefix=f"direct_{uuid.uuid4().hex}_", suffix=ext, delete=False)
            try:
                for chunk in r.iter_bytes(chunk_size=1024 * 256):
                    if chunk:
                        tmp.write(chunk)
            finally:
                tmp.close()
            return tmp.name


def _update_job(job_id: str, fields: dict):
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.table("direct_post_jobs").update(fields).eq("id", job_id).execute()


def post_from_url(
    job_id: str,
    video_url: str,
    caption: str,
    tiktok_open_id: str,
) -> dict:
    """
    1. Mark job as 'posting'
    2. Download the MP4 from `video_url` to a temp file
    3. Use the existing post_video() to upload to TikTok as a draft
    4. Mark job as 'posted' (with publish_id) or 'failed' (with error_message)
    """
    temp_path: Optional[str] = None
    try:
        _update_job(job_id, {"status": "posting", "error_message": None})

        access_token = _resolve_access_token(tiktok_open_id)
        temp_path = _download_to_temp(video_url)

        # post_video expects a list of hashtags; we don't auto-generate them here,
        # so the caption is the source of truth and tags is left empty.
        result = post_video(
            access_token=access_token,
            video_path=temp_path,
            title=caption,
            tags=[],
            caption=caption,
            scheduled_time=None,
        )

        _update_job(
            job_id,
            {
                "status": "posted",
                "tiktok_publish_id": result.get("publish_id"),
            },
        )
        return {"status": "posted", "publish_id": result.get("publish_id")}

    except Exception as e:
        _update_job(job_id, {"status": "failed", "error_message": str(e)})
        return {"status": "failed", "error_message": str(e)}
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
