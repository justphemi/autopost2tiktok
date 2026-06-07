
from app.worker.celery_app import celery_app
from app.database import supabase
from app.services import downloader, ffmpeg, groq, tiktok as tiktok_service, storage
from datetime import datetime, timezone
import os, traceback

def update_job(job_id: str, data: dict):
    supabase.table("jobs").update(data).eq("id", job_id).execute()

@celery_app.task(bind=True, max_retries=2)
def process_video_job(self, job_id: str, user_id: str):
    raw_path = None
    processed_path = None

    try:
        res = supabase.table("jobs").select("*").eq("id", job_id).single().execute()
        job = res.data
        if not job:
            raise Exception(f"Job {job_id} not found in database.")

        update_job(job_id, {"status": "downloading"})
        raw_path = downloader.download_video(job["url"], job_id)
        update_job(job_id, {"status": "processing"})

        processed_path = ffmpeg.make_unique(raw_path, job_id)
        update_job(job_id, {"status": "generating_metadata"})

        metadata = groq.generate_metadata(job["url"])
        title = metadata["title"]
        tags = metadata["tags"]
        caption = metadata["caption"]
        update_job(job_id, {
            "status": "uploading",
            "title": title,
            "tags": tags,
        })

        video_url = storage.upload_video(processed_path, job_id)

        acc_res = supabase.table("tiktok_accounts").select("*").eq("tiktok_open_id", job["tiktok_account_id"]).single().execute()
        account = acc_res.data
        if not account:
            raise Exception(f"TikTok account {job['tiktok_account_id']} not found. Please reconnect your account in Settings.")

        update_job(job_id, {"status": "posting"})

        scheduled_time = job.get("scheduled_time")
        # post_result = tiktok_service.post_video(
        #     access_token=account["access_token"],
        #     video_url=video_url,
        #     title=title,
        #     tags=tags,
        #     caption=caption,
        #     scheduled_time=scheduled_time,
        # )
        post_result = tiktok_service.post_video(
            access_token=account["access_token"],
            video_path=processed_path,   
            title=title,
            tags=tags,
            caption=caption,
            scheduled_time=scheduled_time,
        )

        update_job(job_id, {
            "status": "posted",
            "tiktok_post_url": post_result.get("share_url"),
            "posted_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"[TASK ERROR] Job {job_id}: {error_detail}")

        if self.request.retries < self.max_retries:
            # Not the last retry — mark as retrying, not failed
            update_job(job_id, {
                "status": "retrying",
                "error_message": f"Attempt {self.request.retries + 1} failed, retrying... {str(e)}",
            })
            raise self.retry(exc=e, countdown=30)
        else:
            # Final attempt failed — mark as permanently failed
            update_job(job_id, {
                "status": "failed",
                "error_message": str(e),
            })

    finally:
        for path in [raw_path, processed_path]:
            if path and os.path.exists(path):
                os.remove(path)