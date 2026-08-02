"""
Direct-post routes: submit a Supabase video URL + caption and post to TikTok as a draft.
These endpoints do NOT touch the existing clipper pipeline — they reuse the same
tiktok_accounts table and the existing post_video() service.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import verify_token
from app.database import supabase
from app.services.tiktok_direct import post_from_url

router = APIRouter(prefix="/tiktok", tags=["tiktok-direct"])


class DirectPostSubmit(BaseModel):
    video_url: str = Field(..., min_length=1)
    caption: str = Field(..., min_length=1, max_length=2200)
    tiktok_account_id: str = Field(..., min_length=1)


class DirectPostRetry(BaseModel):
    tiktok_account_id: str = Field(..., min_length=1)


@router.post("/post-from-url")
def submit_direct_post(body: DirectPostSubmit, user=Depends(verify_token)):
    """
    1. Validate the URL is reachable (HEAD request).
    2. Insert a row in direct_post_jobs.
    3. Run the post synchronously (fast: just download + upload to TikTok).
    4. Return the final job state so the UI can show posted / failed immediately.
    """
    url = body.video_url.strip()
    caption = body.caption.strip()
    account_id = body.tiktok_account_id.strip()

    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="video_url must start with http:// or https://")

    # Quick reachability check so we fail fast with a clear error
    try:
        import httpx
        head = httpx.head(url, follow_redirects=True, timeout=15)
        if head.status_code >= 400:
            raise HTTPException(
                status_code=400,
                detail=f"Could not reach the video URL (HTTP {head.status_code}). Make sure the bucket/file is public.",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not reach the video URL: {str(e)}")

    job_id = str(uuid.uuid4())
    try:
        supabase.table("direct_post_jobs").insert({
            "id": job_id,
            "user_id": user["user_id"],
            "video_url": url,
            "caption": caption,
            "tiktok_account_id": account_id,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save job: {str(e)}")

    # Run the post right now. This is just a download + HTTP upload to TikTok,
    # so it does not need a worker queue the way the clipper flow does.
    result = post_from_url(
        job_id=job_id,
        video_url=url,
        caption=caption,
        tiktok_open_id=account_id,
    )
    return {"job_id": job_id, **result}


@router.post("/retry/{job_id}")
def retry_direct_post(job_id: str, user=Depends(verify_token)):
    """Re-run a previously failed direct-post job."""
    try:
        res = (
            supabase.table("direct_post_jobs")
            .select("*")
            .eq("id", job_id)
            .eq("user_id", user["user_id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch job: {str(e)}")

    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found.")
    job = res.data

    result = post_from_url(
        job_id=job_id,
        video_url=job["video_url"],
        caption=job["caption"],
        tiktok_open_id=job["tiktok_account_id"],
    )
    return {"job_id": job_id, **result}


@router.get("/direct-jobs")
def list_direct_jobs(user=Depends(verify_token)):
    """List all direct-post jobs for the current user, newest first."""
    try:
        res = (
            supabase.table("direct_post_jobs")
            .select("*")
            .eq("user_id", user["user_id"])
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return {"jobs": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch jobs: {str(e)}")
