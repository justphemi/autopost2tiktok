from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from jose.backends import RSAKey
import httpx
import base64
from app.config import settings

security = HTTPBearer()

# Cache the JWKS so we don't fetch it on every request
_jwks_cache: dict = {}

def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    res = httpx.get(url)
    res.raise_for_status()
    _jwks_cache = res.json()
    return _jwks_cache

def _get_public_key(token: str):
    """Pick the right key from JWKS based on the token's kid header."""
    header = jwt.get_unverified_header(token)
    jwks = _get_jwks()
    for key in jwks.get("keys", []):
        if key["kid"] == header.get("kid"):
            return key
    raise HTTPException(status_code=401, detail="No matching public key found for token.")

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")

        if alg == "HS256":
            # Older Supabase projects or custom JWT secret config
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False}
            )
        elif alg in ("RS256", "ES256"):
            # Newer Supabase projects using asymmetric keys (RS256 or ES256)
            public_key = _get_public_key(token)
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256", "ES256"],
                options={"verify_aud": False}
    )
        else:
            raise HTTPException(status_code=401, detail=f"Unsupported token algorithm: {alg}")

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing user ID. Please log in again.")

        return {"user_id": user_id}

    except HTTPException:
        raise
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}. Please log in again.")