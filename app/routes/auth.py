from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.database import supabase

router = APIRouter(prefix="/auth", tags=["auth"])

class LoginRequest(BaseModel):
    email: str
    password: str

class ResetRequest(BaseModel):
    email: str

def _is_wrong_password_error(msg: str) -> bool:
    """Supabase throws this when the user exists but creds are invalid."""
    return "invalid login credentials" in msg or "invalid credentials" in msg

def _is_user_exists_error(msg: str) -> bool:
    """Supabase throws this on sign_up when the email is already registered."""
    return any(phrase in msg for phrase in [
        "already registered",
        "user already exists",
        "email address is already",
    ])

@router.post("/login")
def login(body: LoginRequest):
    email = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    # ── Step 1: Attempt sign-in ──────────────────────────────────────────────
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if res.session:
            return {
                "access_token": res.session.access_token,
                "user": {"id": res.user.id, "email": res.user.email},
                "is_new": False,
            }
    except Exception as signin_err:
        signin_msg = str(signin_err).lower()
        if not _is_wrong_password_error(signin_msg):
            raise HTTPException(status_code=400, detail=f"Sign-in error: {str(signin_err)}")
        # Wrong creds error — could be bad password OR deleted/ghost account.
        # Don't reject yet — attempt signup to find out which.

    # ── Step 2: Attempt signup ───────────────────────────────────────────────
    try:
        new = supabase.auth.sign_up({"email": email, "password": password})

        if new.session:
            return {
                "access_token": new.session.access_token,
                "user": {"id": new.user.id, "email": new.user.email},
                "is_new": True,
            }

    except Exception as signup_err:
        signup_msg = str(signup_err).lower()

        # Email exists and is active — sign-in failed due to wrong password.
        if _is_user_exists_error(signup_msg):
            raise HTTPException(
                status_code=401,
                detail="Wrong password for this account. Try again or reset it.",
            )

        raise HTTPException(
            status_code=400,
            detail=f"Could not create account: {str(signup_err)}",
        )

    raise HTTPException(status_code=500, detail="Login failed for an unknown reason. Please try again.")

@router.post("/reset-password")
def reset_password(body: ResetRequest):
    try:
        supabase.auth.reset_password_email(body.email.strip().lower())
        return {"message": f"Password reset email sent to {body.email}. Check your inbox."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not send reset email: {str(e)}")