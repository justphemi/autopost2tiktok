from app.database import supabase
import os

BUCKET = "boltreels-videos"

def upload_video(file_path: str, job_id: str) -> str:
    file_name = f"{job_id}_processed.mp4"
    try:
        with open(file_path, "rb") as f:
            supabase.storage.from_(BUCKET).upload(
                file_name,
                f,
                {"content-type": "video/mp4", "upsert": "true"}  # 👈 overwrite on retry
            )
        url = supabase.storage.from_(BUCKET).get_public_url(file_name)
        return url
    except Exception as e:
        raise Exception(f"Failed to upload processed video to storage: {str(e)}")

def delete_video(job_id: str):
    file_name = f"{job_id}_processed.mp4"
    try:
        supabase.storage.from_(BUCKET).remove([file_name])
    except Exception:
        pass