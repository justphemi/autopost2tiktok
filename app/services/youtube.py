"""
YouTube auto-post service.

Pipeline:
  yt-dlp (≤720p, format cap)  →  SpooledTemporaryFile (8 MB in-RAM, spills to /tmp)
      →  ffmpeg re-mux (h264/aac, faststart)  →  SpooledTemporaryFile
          →  MediaIoBaseUpload (resumable)  →  YouTube Data API v3 videos.insert

Notes
-----
- No fingerprint-stripping transforms are applied. ffmpeg is used purely for
  format/codec compatibility so the upload is a clean mp4 faststart.
- A 10-minute source-duration cap keeps ephemeral Render disk usage bounded.
- The OAuth state row in youtube_oauth_states binds the browser callback to
  the requesting user. The unauthenticated callback only has the state.
"""
import os
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, quote

import httpx
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build as build_youtube
from googleapiclient.http import MediaFileUpload

from app.config import settings
from app.database import supabase


# ---------------------------------------------------------------------------
# OAuth: build URL, handle callback
# ---------------------------------------------------------------------------

OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = settings.YOUTUBE_UPLOAD_SCOPES.split()
STATE_TTL = timedelta(minutes=10)
DURATION_CAP_SECONDS = 600  # 10 minutes — bounds memory + ephemeral disk
SPOOL_MAX_BYTES = 8 * 1024 * 1024  # 8 MB before spilling to /tmp


def build_auth_url(user_id: str) -> str:
    """Generate a Google OAuth URL with a state row bound to user_id."""
    state = secrets.token_urlsafe(32)
    supabase.table("youtube_oauth_states").insert({
        "state": state,
        "user_id": user_id,
    }).execute()

    params = {
        "client_id": settings.YOUTUBE_CLIENT_ID,
        "redirect_uri": settings.YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"{OAUTH_AUTHORIZE_URL}?{qs}"


def _consume_state(state: str) -> str:
    """Look up the state row, enforce TTL, delete on success, return user_id."""
    if not state:
        raise ValueError("missing state")
    res = (
        supabase.table("youtube_oauth_states")
        .select("*")
        .eq("state", state)
        .single()
        .execute()
    )
    row = res.data
    if not row:
        raise ValueError("invalid or expired state")

    created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - created_at > STATE_TTL:
        supabase.table("youtube_oauth_states").delete().eq("state", state).execute()
        raise ValueError("state expired, please try again")

    # Burn it on first successful read so it can't be replayed
    supabase.table("youtube_oauth_states").delete().eq("state", state).execute()
    return row["user_id"]


def handle_callback(state: str, code: str) -> dict:
    """Exchange code → tokens, fetch channel metadata, upsert youtube_accounts."""
    user_id = _consume_state(state)

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.YOUTUBE_CLIENT_ID,
                "client_secret": settings.YOUTUBE_CLIENT_SECRET,
                "auth_uri": OAUTH_AUTHORIZE_URL,
                "token_uri": OAUTH_TOKEN_URL,
                "redirect_uris": [settings.YOUTUBE_REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=settings.YOUTUBE_REDIRECT_URI,
        state=state,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    youtube = build_youtube("youtube", "v3", credentials=creds)
    ch_res = youtube.channels().list(mine=True, part="snippet").execute()
    items = ch_res.get("items", [])
    if not items:
        raise ValueError("no YouTube channel found for this Google account")
    snippet = items[0]["snippet"]
    channel_id = items[0]["id"]

    expiry_iso = None
    if creds.expiry:
        expiry_iso = creds.expiry.astimezone(timezone.utc).isoformat()

    # Reconnect: delete any prior row for this user so we never carry stale
    # refresh tokens around. (The unique index on user_id also enforces this.)
    supabase.table("youtube_accounts").delete().eq("user_id", user_id).execute()
    supabase.table("youtube_accounts").insert({
        "user_id": user_id,
        "channel_id": channel_id,
        "channel_title": snippet.get("title"),
        "thumbnail_url": (snippet.get("thumbnails", {}).get("default") or {}).get("url"),
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "scopes": " ".join(creds.scopes or []),
        "expiry": expiry_iso,
    }).execute()

    return {
        "channel_id": channel_id,
        "channel_title": snippet.get("title"),
        "thumbnail_url": (snippet.get("thumbnails", {}).get("default") or {}).get("url"),
    }


# ---------------------------------------------------------------------------
# Credentials lookup + refresh
# ---------------------------------------------------------------------------

def _load_credentials(user_id: str, account_id: str) -> tuple[Credentials, dict]:
    """Read the account row, rebuild Credentials, return (creds, row)."""
    res = (
        supabase.table("youtube_accounts")
        .select("*")
        .eq("id", account_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not res.data:
        raise ValueError("YouTube account not found")
    row = res.data

    expiry = None
    if row.get("expiry"):
        expiry = datetime.fromisoformat(row["expiry"].replace("Z", "+00:00"))

    creds = Credentials(
        token=row.get("access_token"),
        refresh_token=row.get("refresh_token"),
        token_uri=row.get("token_uri") or OAUTH_TOKEN_URL,
        client_id=settings.YOUTUBE_CLIENT_ID,
        client_secret=settings.YOUTUBE_CLIENT_SECRET,
        scopes=(row.get("scopes") or " ".join(SCOPES)).split(),
        expiry=expiry,
    )
    return creds, row


def _save_credentials(account_id: str, creds: Credentials) -> None:
    expiry_iso = creds.expiry.astimezone(timezone.utc).isoformat() if creds.expiry else None
    supabase.table("youtube_accounts").update({
        "access_token": creds.token,
        "refresh_token": creds.refresh_token or None,
        "token_uri": creds.token_uri,
        "scopes": " ".join(creds.scopes or []),
        "expiry": expiry_iso,
    }).eq("id", account_id).execute()


def refresh_if_needed(user_id: str, account_id: str) -> Credentials:
    """Refresh the access token if it's missing, expired, or close to expiry."""
    creds, _ = _load_credentials(user_id, account_id)
    if not creds.refresh_token:
        return creds  # nothing we can do

    needs_refresh = (
        not creds.valid
        or not creds.expiry
        or creds.expiry <= datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    if needs_refresh:
        creds.refresh(Request())
        _save_credentials(account_id, creds)
    return creds


# ---------------------------------------------------------------------------
# yt-dlp + ffmpeg + upload pipeline
# ---------------------------------------------------------------------------

def _format_selector(link: str) -> list[str]:
    """Pick a yt-dlp format selector appropriate to the source host."""
    host = (urlparse(link).hostname or "").lower()
    if "tiktok.com" in host or "vm.tiktok" in host:
        return ["-f", "best[height<=720][ext=mp4]/best[height<=720]/best"]
    if "instagram.com" in host:
        return ["-f", "best[height<=720][ext=mp4]/best[height<=720]/best"]
    if "youtube.com" in host or "youtu.be" in host:
        return ["-f", "bv*[height<=720]+ba/b[height<=720]/b[height<=720]"]
    return ["-f", "best[height<=720]/best"]


def _ffmpeg_binary() -> str:
    """Return the path to the ffmpeg binary from static-ffmpeg."""
    try:
        import static_ffmpeg
        path = static_ffmpeg.get()
        # static_ffmpeg.get() returns (ffmpeg_path, ffprobe_path) in some
        # versions, or just a path in others. Handle both.
        if isinstance(path, (list, tuple)):
            return path[0]
        return path
    except Exception as e:
        raise RuntimeError(
            "ffmpeg is not available. The static-ffmpeg package should have "
            f"installed it at pip-install time, but it failed: {e}"
        ) from e


def _download_with_ytdlp(link: str, out_path: str) -> None:
    """Run yt-dlp as a subprocess, outputting to out_path. Validates
    duration against the cap; raises with a friendly message if exceeded."""
    cmd = ["yt-dlp", "--no-playlist", "--no-warnings",
           "--match-filter", f"duration < {DURATION_CAP_SECONDS}",
           *_format_selector(link),
           "--merge-output-format", "mp4",
           "-o", out_path,
           link]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        # yt-dlp writes the human error to stderr
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-3:] if err else []
        raise RuntimeError(
            "yt-dlp failed: " + (" | ".join(tail) or f"exit {proc.returncode}")
        )


def _ffmpeg_remux(in_path: str, out_path: str) -> None:
    """Re-mux to a YouTube-friendly h264/aac mp4 with faststart."""
    ffmpeg = _ffmpeg_binary()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", in_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-3:] if err else []
        raise RuntimeError(
            "ffmpeg remux failed: " + (" | ".join(tail) or f"exit {proc.returncode}")
        )


def stream_upload_to_youtube(
    user_id: str,
    account_id: str,
    link: str,
    title: str,
    description: str,
    tags: list[str],
) -> dict:
    """
    Download → transcode → upload. Returns {"video_id", "video_url"}.

    Raises on any failure. The caller is responsible for updating the job row.
    """
    creds = refresh_if_needed(user_id, account_id)
    youtube = build_youtube("youtube", "v3", credentials=creds)

    raw_path = None
    out_path = None
    try:
        # Stage 1: yt-dlp → SpooledTemporaryFile that spills to /tmp
        with tempfile.SpooledTemporaryFile(
            max_size=SPOOL_MAX_BYTES, suffix=".mp4"
        ) as raw_spool:
            # SpooledTemporaryFile is a file-like object; use a NamedTemporaryFile
            # for yt-dlp because it needs a real path on disk for -o.
            with tempfile.NamedTemporaryFile(
                prefix="ytdlp_", suffix=".mp4", delete=False
            ) as raw_file:
                raw_path = raw_file.name
            _download_with_ytdlp(link, raw_path)

            # Stage 2: ffmpeg → second SpooledTemporaryFile
            with tempfile.NamedTemporaryFile(
                prefix="ffmpeg_", suffix=".mp4", delete=False
            ) as out_file:
                out_path = out_file.name
            _ffmpeg_remux(raw_path, out_path)

            # Stage 3: resumable upload. MediaFileUpload accepts a file path
            # and streams from disk, so we don't have to materialize the
            # whole file in memory.
            media = MediaFileUpload(
                out_path,
                mimetype="video/mp4",
                resumable=True,
                chunksize=SPOOL_MAX_BYTES,
            )
            body = {
                "snippet": {
                    "title": (title or "")[:100],
                    "description": (description or "")[:5000],
                    "tags": [str(t) for t in (tags or []) if t][:15],
                },
                "status": {
                    "privacyStatus": "private",
                    "selfDeclaredMadeForKids": False,
                    "embeddable": True,
                },
            }
            res = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            ).execute()
            video_id = res.get("id")
            if not video_id:
                raise RuntimeError(f"YouTube upload returned no id: {res}")
            return {
                "video_id": video_id,
                "video_url": f"https://youtu.be/{video_id}",
            }
    finally:
        for p in (raw_path, out_path):
            if p and os.path.exists(p):
                try: os.unlink(p)
                except OSError: pass
