import yt_dlp
import os
import shutil
import tempfile
import base64

DOWNLOAD_DIR = "/tmp/boltreels_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

_LOCAL_YT_COOKIES = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../youtube_cookies.txt")
)
_LOCAL_IG_COOKIES = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../instagram_cookies.txt")
)

def get_cookies_path(platform: str = "youtube") -> str | None:
    """
    Returns path to cookies file for the given platform.
    Checks env var first (Render), then local file (dev).
    platform: "youtube" or "instagram"
    """
    env_key = "YOUTUBE_COOKIES_CONTENT" if platform == "youtube" else "INSTAGRAM_COOKIES_CONTENT"
    local_path = _LOCAL_YT_COOKIES if platform == "youtube" else _LOCAL_IG_COOKIES

    content = os.getenv(env_key)
    if content and content.strip():
        try:
            decoded = base64.b64decode(content).decode("utf-8")
        except Exception:
            decoded = content
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        tmp.write(decoded)
        tmp.close()
        print(f">>> [{platform}] cookies written to {tmp.name}, size: {os.path.getsize(tmp.name)} bytes")
        return tmp.name

    if os.path.exists(local_path):
        print(f">>> [{platform}] using local cookies file at {local_path}")
        return local_path

    print(f">>> [{platform}] no cookies found")
    return None


def download_video(url: str, job_id: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, f"{job_id}_raw.mp4")
    ffmpeg_path = shutil.which("ffmpeg")
    proxy = os.getenv("YTDLP_PROXY")

    # Detect platform from URL
    is_instagram = "instagram.com" in url
    platform = "instagram" if is_instagram else "youtube"
    cookies_path = get_cookies_path(platform)

    print(f">>> ffmpeg found at: {ffmpeg_path}")
    print(f">>> platform: {platform}")
    print(f">>> proxy: {proxy or 'none'}")
    print(f">>> downloading: {url}")

    ydl_opts = {
        "outtmpl": output_path,
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/18/best[ext=mp4]/best"
            if ffmpeg_path else
            "18/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "format_sort": ["res:1080", "ext:mp4:m4a", "tbr", "asr"],
        "quiet": False,
        "no_warnings": False,
        # iOS client works best for YouTube bot detection bypass
        # Instagram doesn't use extractor_args the same way
        **({"extractor_args": {"youtube": {"player_client": ["ios"]}}} if not is_instagram else {}),
        # Only set proxy if we have one — None value causes issues
        **({"proxy": proxy} if proxy else {}),
        **({"cookiefile": cookies_path} if cookies_path else {}),
        **({"ffmpeg_location": ffmpeg_path} if ffmpeg_path else {}),
    }

    tmp_cookie_path = None
    if cookies_path and cookies_path.startswith(tempfile.gettempdir()):
        tmp_cookie_path = cookies_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        err = str(e)
        # Proxy error — most specific, check first
        if "proxy" in err.lower() or "ProxyError" in err or "Unable to connect to proxy" in err:
            raise Exception(
                "Proxy connection failed. The proxy may be dead or blocked. "
                "Update YTDLP_PROXY in your server environment."
            )
        # Instagram specific
        if is_instagram and ("login" in err.lower() or "rate-limit" in err.lower() or "not available" in err.lower()):
            raise Exception(
                "Instagram download failed. The reel may be private, or Instagram is rate-limiting the server. "
                "Try again in a few minutes or use a different reel."
            )
        # YouTube bot detection
        if "Sign in" in err or "confirm you're not a bot" in err:
            raise Exception(
                "YouTube is blocking the server as a bot. "
                "This usually means the proxy is not working or cookies have expired."
            )
        if "login" in err.lower():
            raise Exception(
                "This video requires a login to download. It may be private or age-restricted."
            )
        if "Private video" in err:
            raise Exception("This video is private and cannot be downloaded.")
        if "not available" in err.lower() or "blocked" in err.lower():
            raise Exception("This video is not available or is geo-restricted. Try a different video.")
        if "age" in err.lower():
            raise Exception("This video is age-restricted. Add your YouTube cookies to download it.")
        raise Exception(f"Download failed: {err[:300]}")
    finally:
        if tmp_cookie_path and os.path.exists(tmp_cookie_path):
            os.remove(tmp_cookie_path)

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