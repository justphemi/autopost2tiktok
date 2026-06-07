import yt_dlp
import os
import shutil

DOWNLOAD_DIR = "/tmp/boltreels_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def download_video(url: str, job_id: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, f"{job_id}_raw.mp4")

    # Check ffmpeg is actually available
    ffmpeg_path = shutil.which("ffmpeg")

    ydl_opts = {
        "outtmpl": output_path,
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/18/best[ext=mp4]/best"
            if ffmpeg_path else
            "18/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "cookiesfrombrowser": ("chrome",),  # uses your Chrome YouTube login for restricted videos
        **({"ffmpeg_location": ffmpeg_path} if ffmpeg_path else {}),
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }

    print(f">>> ffmpeg found at: {ffmpeg_path}")
    print(f">>> using format: {ydl_opts['format']}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        err = str(e)
        if "Sign in" in err or "login" in err.lower():
            raise Exception("This video requires a YouTube login.")
        if "Private video" in err:
            raise Exception("This video is private.")
        raise Exception(f"Download failed: {err}")

    if not os.path.exists(output_path):
        matches = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(job_id)]
        if matches:
            os.rename(os.path.join(DOWNLOAD_DIR, matches[0]), output_path)
        else:
            raise Exception("Download completed but output file not found.")

    return output_path