
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from app.auth import verify_token
from app.database import supabase
from app.config import settings
import httpx
import secrets

router = APIRouter(prefix="/tiktok", tags=["tiktok"])

@router.get("/connect")
def connect_tiktok(user=Depends(verify_token)):
    """Returns TikTok OAuth URL for the frontend to redirect to."""
    state = secrets.token_urlsafe(16)
    auth_url = (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={settings.TIKTOK_CLIENT_KEY}"
        f"&scope=user.info.basic,video.upload,video.publish"
        f"&response_type=code"
        f"&redirect_uri={settings.TIKTOK_REDIRECT_URI}"
        f"&state={state}"
    )
    # Temporarily log both so you can compare them exactly
    print(">>> REDIRECT URI BEING SENT:", settings.TIKTOK_REDIRECT_URI)
    print(">>> FULL AUTH URL:", auth_url)
    return {"auth_url": auth_url}

@router.get("/callback")
async def tiktok_callback(code: str, state: str, request: Request):
    print(">>> CALLBACK HIT, code:", code[:20], "state:", state) 
    """TikTok redirects here after user approves. Exchange code for tokens."""
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={
                    "client_key": settings.TIKTOK_CLIENT_KEY,
                    "client_secret": settings.TIKTOK_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.TIKTOK_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            token_data = res.json()
            print(">>> TIKTOK TOKEN RESPONSE:", token_data)  # 👈 add this

        if "access_token" not in token_data:
            print(">>> TOKEN ERROR:", token_data)  # 👈 and this
            return RedirectResponse(f"{settings.FRONTEND_URL}/settings?error=tiktok_auth_failed")

        access_token = token_data["access_token"]

        async with httpx.AsyncClient() as client:
            user_res = await client.get(
                "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,avatar_url",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            user_data = user_res.json().get("data", {}).get("user", {})
            print(">>> TIKTOK USER DATA:", user_data)  # 👈 and this

        supabase.table("tiktok_accounts").upsert({
            "tiktok_open_id": user_data.get("open_id"),
            "display_name": user_data.get("display_name"),
            "avatar_url": user_data.get("avatar_url"),
            "access_token": access_token,
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": token_data.get("expires_in"),
        }).execute()

        return RedirectResponse(f"{settings.FRONTEND_URL}/settings?tiktok=connected")

    except Exception as e:
        print(">>> TIKTOK CALLBACK EXCEPTION:", str(e))  # 👈 and this
        import traceback
        traceback.print_exc()  # 👈 full stack trace
        return RedirectResponse(f"{settings.FRONTEND_URL}/settings?error=tiktok_callback_error")
@router.get("/accounts")
def get_tiktok_accounts(user=Depends(verify_token)):
    try:
        res = supabase.table("tiktok_accounts").select("tiktok_open_id, display_name, avatar_url").execute()
        return {"accounts": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch TikTok accounts: {str(e)}")
