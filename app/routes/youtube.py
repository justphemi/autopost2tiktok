"""
YouTube routes: OAuth connect/callback, account listing + refresh,
metadata generation, post-from-link, retry, and job listing.
"""
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth import verify_token
from app.config import settings
from app.database import supabase
from app.services.groq import generate_metadata
from app.services.youtube import (
    build_auth_url as service_build_auth_url,
    handle_callback as service_handle_callback,
    refresh_if_needed,
    stream_upload_to_youtube,
)

router = APIRouter(prefix="/youtube", tags=["youtube"])

ALLOWED_HOSTS = (
    "tiktok.com", "vm.tiktok.com",
    "instagram.com",
    "youtube.com", "youtu.be",
)


def _is_acceptable_link(url: str) -> bool:
    try:
        u = urlparse(url.strip())
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


# ---------------------------------------------------------------------------
# Connect / callback
# ---------------------------------------------------------------------------

@router.get("/connect")
def connect(user=Depends(verify_token)):
    """Return the Google OAuth URL for the frontend to redirect to."""
    try:
        url = service_build_auth_url(user["user_id"])
        return {"auth_url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build auth URL: {e}")


@router.get("/callback")
async def callback(code: str = Query(...), state: str = Query(...)):
    """Browser redirect target after Google consent. No JWT here; the state
    row in youtube_oauth_states is the binding to the requesting user."""
    try:
        info = service_handle_callback(state=state, code=code)
    except Exception as e:
        return RedirectResponse(
            f"{settings.FRONTEND_URL}/youtube?error=youtube_callback_error&detail={e}"
        )
    return RedirectResponse(
        f"{settings.FRONTEND_URL}/youtube?connected=1&channel={info.get('channel_id', '')}"
    )


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@router.get("/accounts")
def list_accounts(user=Depends(verify_token)):
    try:
        res = (
            supabase.table("youtube_accounts")
            .select("id, channel_id, channel_title, thumbnail_url, expiry, created_at")
            .eq("user_id", user["user_id"])
            .order("created_at", desc=True)
            .execute()
        )
        return {"accounts": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch accounts: {e}")


class RefreshBody(BaseModel):
    youtube_account_id: str = Field(..., min_length=1)


@router.post("/refresh")
def refresh(body: RefreshBody, user=Depends(verify_token)):
    try:
        creds = refresh_if_needed(user["user_id"], body.youtube_account_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {e}")
    return {
        "ok": True,
        "expires_at": creds.expiry.astimezone(timezone.utc).isoformat() if creds.expiry else None,
    }


@router.delete("/accounts/{account_id}")
def disconnect(account_id: str, user=Depends(verify_token)):
    try:
        supabase.table("youtube_accounts").delete() \
            .eq("id", account_id).eq("user_id", user["user_id"]).execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {e}")


# ---------------------------------------------------------------------------
# Metadata generation
# ---------------------------------------------------------------------------

class GenerateBody(BaseModel):
    link: str = Field(..., min_length=1)
    description: str = Field(default="", max_length=2000)


@router.post("/generate-metadata")
def generate(body: GenerateBody, user=Depends(verify_token)):
    if not _is_acceptable_link(body.link):
        raise HTTPException(
            status_code=400,
            detail="link must be a TikTok, Instagram, or YouTube URL",
        )
    try:
        return generate_metadata(body.link.strip(), body.description)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Metadata generation failed: {e}")


# ---------------------------------------------------------------------------
# Post from link
# ---------------------------------------------------------------------------

class PostBody(BaseModel):
    link: str = Field(..., min_length=1)
    description: str = Field(default="", max_length=2000)
    youtube_account_id: str = Field(..., min_length=1)
    title: str = Field(default="", max_length=100)
    description_youtube: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)


def _update_job(job_id: str, fields: dict) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.table("youtube_jobs").update(fields).eq("id", job_id).execute()


@router.post("/post-from-link")
def post_from_link(body: PostBody, user=Depends(verify_token)):
    if not _is_acceptable_link(body.link):
        raise HTTPException(
            status_code=400,
            detail="link must be a TikTok, Instagram, or YouTube URL",
        )

    # Verify the account belongs to the user
    acct = (
        supabase.table("youtube_accounts")
        .select("id")
        .eq("id", body.youtube_account_id)
        .eq("user_id", user["user_id"])
        .single()
        .execute()
    )
    if not acct.data:
        raise HTTPException(status_code=404, detail="YouTube account not found")

    job_id = str(uuid.uuid4())
    try:
        supabase.table("youtube_jobs").insert({
            "id": job_id,
            "user_id": user["user_id"],
            "source_link": body.link.strip(),
            "user_description": body.description.strip(),
            "youtube_account_id": body.youtube_account_id,
            "generated_title": body.title.strip() or None,
            "generated_description": body.description_youtube.strip() or None,
            "generated_tags": body.tags or None,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save job: {e}")

    _update_job(job_id, {"status": "posting", "error_message": None})

    try:
        result = stream_upload_to_youtube(
            user_id=user["user_id"],
            account_id=body.youtube_account_id,
            link=body.link.strip(),
            title=body.title,
            description=body.description_youtube,
            tags=body.tags,
        )
        _update_job(job_id, {
            "status": "posted",
            "youtube_video_id": result["video_id"],
            "youtube_video_url": result["video_url"],
        })
        return {
            "job_id": job_id,
            "status": "posted",
            "video_id": result["video_id"],
            "video_url": result["video_url"],
        }
    except Exception as e:
        _update_job(job_id, {"status": "failed", "error_message": str(e)})
        return {
            "job_id": job_id,
            "status": "failed",
            "error_message": str(e),
        }


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

@router.post("/retry/{job_id}")
def retry(job_id: str, user=Depends(verify_token)):
    try:
        res = (
            supabase.table("youtube_jobs")
            .select("*")
            .eq("id", job_id)
            .eq("user_id", user["user_id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch job: {e}")
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = res.data

    # If we never generated metadata, there's nothing to upload — fail loudly
    if not job.get("generated_title") or not job.get("generated_description"):
        raise HTTPException(
            status_code=400,
            detail="This job has no generated metadata to retry. Resubmit from the form.",
        )

    _update_job(job_id, {"status": "posting", "error_message": None})

    try:
        result = stream_upload_to_youtube(
            user_id=user["user_id"],
            account_id=job["youtube_account_id"],
            link=job["source_link"],
            title=job["generated_title"],
            description=job["generated_description"],
            tags=job.get("generated_tags") or [],
        )
        _update_job(job_id, {
            "status": "posted",
            "youtube_video_id": result["video_id"],
            "youtube_video_url": result["video_url"],
        })
        return {
            "job_id": job_id,
            "status": "posted",
            "video_id": result["video_id"],
            "video_url": result["video_url"],
        }
    except Exception as e:
        _update_job(job_id, {"status": "failed", "error_message": str(e)})
        return {
            "job_id": job_id,
            "status": "failed",
            "error_message": str(e),
        }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@router.get("/jobs")
def list_jobs(user=Depends(verify_token)):
    try:
        res = (
            supabase.table("youtube_jobs")
            .select("*")
            .eq("user_id", user["user_id"])
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return {"jobs": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch jobs: {e}")
