import yt_dlp
import os
import shutil
import tempfile

DOWNLOAD_DIR = "/tmp/boltreels_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Local cookies file path (for local dev)
_LOCAL_COOKIES = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../youtube_cookies.txt")
)

def get_cookies_path() -> str | None:
    """
    Returns a path to a valid YouTube cookies file.
    - On Render: writes YOUTUBE_COOKIES_CONTENT env var to a temp file
    - Locally: uses youtube_cookies.txt in the repo root if it exists
    - Returns None if no cookies available (will still try without them)
    """
    # Production: cookies stored as env var on Render
    content = os.getenv("YOUTUBE_COOKIES_CONTENT")
    if content and content.strip():
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        tmp.write(content)
        tmp.close()
        print(f">>> using cookies from env var, written to {tmp.name}")
        return tmp.name

    # Local dev: cookies file in repo root
    if os.path.exists(_LOCAL_COOKIES):
        print(f">>> using local cookies file at {_LOCAL_COOKIES}")
        return _LOCAL_COOKIES

    print(">>> no cookies found — attempting download without authentication")
    return None


def download_video(url: str, job_id: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, f"{job_id}_raw.mp4")
    ffmpeg_path = shutil.which("ffmpeg")
    cookies_path = get_cookies_path()

    print(f">>> ffmpeg found at: {ffmpeg_path}")
    print(f">>> downloading: {url}")

    ydl_opts = {
        "outtmpl": output_path,
        # Format fallback chain:
        # 1. Best separate video+audio streams merged (highest quality)
        # 2. Format 18 = reliable 360p combined mp4 (works on most Shorts)
        # 3. Best available mp4
        # 4. Absolute fallback — take anything
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/18/best[ext=mp4]/best"
            if ffmpeg_path else
            "18/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "format_sort": ["res:1080", "ext:mp4:m4a", "tbr", "asr"],
        "quiet": False,
        "no_warnings": False,
        # Use Android + Web client — required for YouTube Shorts
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
        # Inject cookies if available (handles age-restricted / login-required videos)
        **({"cookiefile": cookies_path} if cookies_path else {}),
        # Inject ffmpeg path if found
        **({"ffmpeg_location": ffmpeg_path} if ffmpeg_path else {}),
    }

    tmp_cookie_path = None
    # Track temp file so we can clean it up after download
    if cookies_path and cookies_path.startswith(tempfile.gettempdir()):
        tmp_cookie_path = cookies_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        err = str(e)
        # Specific helpful error messages
        if "Sign in" in err or "login" in err.lower():
            raise Exception(
                "This video requires a YouTube login to download. "
                "Add your YouTube cookies in Settings or try a different video."
            )
        if "Private video" in err:
            raise Exception("This video is private and cannot be downloaded.")
        if "not available" in err.lower() or "blocked" in err.lower():
            raise Exception(
                "This video is not available or is geo-restricted. Try a different video."
            )
        if "age" in err.lower():
            raise Exception(
                "This video is age-restricted. Add your YouTube cookies to download it."
            )
        raise Exception(f"Download failed: {err}")
    finally:
        # Clean up temp cookie file if we created one
        if tmp_cookie_path and os.path.exists(tmp_cookie_path):
            os.remove(tmp_cookie_path)

    # yt-dlp sometimes tweaks the filename — find the actual output file
    if not os.path.exists(output_path):
        matches = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(job_id)]
        if matches:
            actual = os.path.join(DOWNLOAD_DIR, matches[0])
            os.rename(actual, output_path)
            print(f">>> renamed {matches[0]} to expected output path")
        else:
            raise Exception(
                "Download completed but no output file was found. "
                "The video may be private, geo-restricted, or in an unsupported format."
            )

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f">>> download complete: {output_path} ({size_mb:.1f} MB)")

    return output_path