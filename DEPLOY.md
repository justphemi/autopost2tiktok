# Deploying boltreels to Render

This guide walks you through deploying the FastAPI backend and the Vite static frontend to Render, wiring up environment variables, and pointing the OAuth callbacks at the live URLs.

## 1. Prerequisites

- A [Render](https://render.com) account
- A [Supabase](https://supabase.com) project (the one in `boltreels-backend/.env` is fine)
- A [Google Cloud](https://console.cloud.google.com) project with OAuth credentials (for YouTube)
- A [TikTok Developer](https://developers.tiktok.com) app (for TikTok)
- A [Groq](https://console.groq.com) API key (free tier is fine)

## 2. One-time Supabase setup

Run the new migration in the Supabase SQL editor (the project at the URL in `SUPABASE_URL`):

```bash
# From this repo, the file is at:
#   supabase_migrations/youtube.sql
```

Open the Supabase dashboard → SQL → New query → paste the file contents → Run. It will:

- Create `youtube_accounts`, `youtube_oauth_states`, and `youtube_jobs` tables
- Set up the `auth.uid() = user_id` RLS policies on the two user-owned tables
- Enable RLS (with no policies) on `youtube_oauth_states`, which is only accessed by the backend service role

## 3. One-time Google Cloud setup (for YouTube)

1. https://console.cloud.google.com → your project → APIs & Services → Library → enable **YouTube Data API v3**
2. APIs & Services → Credentials → Create credentials → OAuth client ID → Web application
3. Authorized redirect URIs — add both:
   - `http://localhost:8000/youtube/callback` (local dev)
   - `https://boltreels-api.onrender.com/youtube/callback` (production)
4. Copy the Client ID and Client Secret.

## 4. Deploy the backend

1. In Render, click **New** → **Blueprint** and point it at this repo.
2. Render will detect `boltreels-backend/render.yaml` and create two services: `boltreels-api` (web) and `boltreels-web` (static).
3. Open the `boltreels-api` service → **Environment** and set the following keys (Render will mark them as secret):
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `JWT_SECRET`
   - `TIKTOK_CLIENT_KEY`
   - `TIKTOK_CLIENT_SECRET`
   - `TIKTOK_REDIRECT_URI` (use `https://boltreels-api.onrender.com/tiktok/callback` for prod)
   - `YOUTUBE_CLIENT_ID`
   - `YOUTUBE_CLIENT_SECRET`
   - `YOUTUBE_REDIRECT_URI` (use `https://boltreels-api.onrender.com/youtube/callback` for prod)
   - `GROQ_API_KEY`
   - `FRONTEND_URL` — **set this AFTER the static site is up**, to the static site's URL
4. Save. Render will trigger a build. Watch the logs; the first build pulls in `static-ffmpeg` which downloads a ~30 MB ffmpeg binary on install.
5. The build will fail with a CORS / config error if `FRONTEND_URL` is wrong, but the service itself will be live.

## 5. Deploy the frontend

Once the backend's public URL is known (e.g. `https://boltreels-api.onrender.com`):

1. In the `boltreels-web` static site → **Environment** → set `VITE_API_URL` to the backend's public URL.
2. Trigger a manual deploy (the value is baked in at build time, so any change requires a rebuild).
3. The site will be at `https://boltreels-web.onrender.com` by default. Test the SPA fallback by navigating to `https://boltreels-web.onrender.com/youtube` directly — it should serve `index.html` (200) and the React app should hydrate.

## 6. Update FRONTEND_URL

Go back to the backend service → set `FRONTEND_URL` to the static site's URL (`https://boltreels-web.onrender.com`). The backend uses this for OAuth callback redirects. Restart the backend.

## 7. Custom domains

For each service, Settings → Custom Domains:

- `boltreels-web`: add `boltreels.com` (or whatever you own)
- `boltreels-api`: add `api.boltreels.com` (or similar)

Then:

- Update `FRONTEND_URL` on the backend to the custom frontend URL
- Add the new callback URLs to the Google Cloud OAuth client (e.g. `https://api.boltreels.com/youtube/callback`)
- Update the static site's `VITE_API_URL` env var to `https://api.boltreels.com` and redeploy

## 8. Free-tier caveat

Render's free plan **spins down after 15 minutes of inactivity**. The first request after a quiet period takes 30–60 seconds to wake up. For an OAuth callback specifically, this means:

- The user clicks "Connect YouTube"
- Google redirects to your callback
- The service is cold-starting
- The page may show "took too long to respond" or time out at the browser level

Workarounds (pick one):

- **Upgrade to a paid plan** (Starter is the cheapest, $7/mo at time of writing). The service stays warm.
- **Set up a free cron pinger** to hit `https://boltreels-api.onrender.com/health` every 14 minutes. Services like cron-job.org offer this for free. The render.yaml currently sets `plan: starter` for the backend specifically to avoid this problem — change it to `free` if you want to test the cold-start behavior or are okay with the cron workaround.

## 9. Updating yt-dlp

yt-dlp's source downloaders break monthly as Instagram/TikTok/YouTube change their download signatures. To update:

```bash
cd boltreels-backend
pip install --upgrade 'yt-dlp>=2024.10.7,<2025.1.1'
```

Then redeploy. If you want a wider upper bound, edit `requirements.txt`.

## 10. Local development

```bash
# From the repo root:
./start.command      # macOS
# or
start.bat            # Windows
```

The local backend uses `http://localhost:8000`. Make sure your `.env` reflects that for the OAuth redirect URIs in Google Cloud.
