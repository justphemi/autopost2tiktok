import httpx
import os
from datetime import datetime
from typing import Optional

def post_video(
    access_token: str,
    video_path: str,
    title: str,
    tags: list,
    caption: str,
    scheduled_time: Optional[str] = None
) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    hashtags = " ".join([f"#{t}" for t in tags[:5]])
    full_caption = f"{caption} {hashtags}"[:2200]
    video_size = os.path.getsize(video_path)

    payload = {
        "post_info": {
            "title": title[:150],
            "description": full_caption,
            "privacy_level": "SELF_ONLY",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        }
    }

    if scheduled_time:
        try:
            dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
            payload["post_info"]["scheduled_publish_time"] = int(dt.timestamp())
            payload["post_info"]["auto_add_music"] = False
        except Exception as e:
            raise Exception(f"Invalid scheduled time format: {str(e)}")

    try:
        with httpx.Client(timeout=60) as client:
            # Step 1: Init the upload
            init_res = client.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                json=payload,
                headers=headers,
            )
            init_data = init_res.json()
            print(">>> TIKTOK INIT:", init_data)

            error = init_data.get("error", {})
            if error.get("code") not in [None, "ok"]:
                raise Exception(f"TikTok init error [{error.get('code')}]: {error.get('message')}")

            upload_url = init_data["data"]["upload_url"]
            publish_id = init_data["data"]["publish_id"]

            # Step 2: Upload the video file
            with open(video_path, "rb") as f:
                video_bytes = f.read()

            upload_res = client.put(
                upload_url,
                content=video_bytes,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
                    "Content-Length": str(video_size),
                },
                timeout=120,
            )
            print(">>> TIKTOK UPLOAD STATUS:", upload_res.status_code, upload_res.text[:200])

            if upload_res.status_code not in (200, 201, 206):
                raise Exception(f"TikTok upload failed: HTTP {upload_res.status_code} — {upload_res.text[:200]}")

            return {"publish_id": publish_id}

    except httpx.TimeoutException:
        raise Exception("TikTok upload timed out. Try again.")
    except httpx.RequestError as e:
        raise Exception(f"Network error contacting TikTok: {str(e)}")