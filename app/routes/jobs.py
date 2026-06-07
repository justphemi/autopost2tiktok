
from fastapi import APIRouter, Depends, HTTPException
from app.models import JobSubmit, JobResponse
from app.auth import verify_token
from app.database import supabase
from app.worker.tasks import process_video_job
from datetime import datetime, timezone
import uuid

router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.post("/submit")
def submit_job(body: JobSubmit, user=Depends(verify_token)):
    user_id = user["user_id"]

    # Validate scheduled time is in the future
    if body.scheduled_time:
        now = datetime.now(timezone.utc)
        if body.scheduled_time.replace(tzinfo=timezone.utc) <= now:
            raise HTTPException(
                status_code=400,
                detail="Scheduled time must be in the future. Please pick a later date/time."
            )

    # Validate URL looks like YouTube or Instagram
    url = body.url.strip()
    if not ("youtube.com" in url or "youtu.be" in url or "instagram.com" in url):
        raise HTTPException(
            status_code=400,
            detail="URL must be a YouTube or Instagram link. Other platforms are not supported yet."
        )

    job_id = str(uuid.uuid4())

    try:
        supabase.table("jobs").insert({
            "id": job_id,
            "user_id": user_id,
            "url": url,
            "platform": body.platform,
            "tiktok_account_id": body.tiktok_account_id,
            "scheduled_time": body.scheduled_time.isoformat() if body.scheduled_time else None,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save job to database: {str(e)}")

    # Queue the background task immediately
    process_video_job.delay(job_id, user_id)

    return {"job_id": job_id, "status": "queued", "message": "Job queued successfully. Processing will begin shortly."}

@router.get("/list")
def list_jobs(user=Depends(verify_token)):
    user_id = user["user_id"]
    try:
        res = supabase.table("jobs").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(50).execute()
        return {"jobs": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch jobs: {str(e)}")

@router.get("/{job_id}")
def get_job(job_id: str, user=Depends(verify_token)):
    user_id = user["user_id"]
    try:
        res = supabase.table("jobs").select("*").eq("id", job_id).eq("user_id", user_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch job: {str(e)}")
