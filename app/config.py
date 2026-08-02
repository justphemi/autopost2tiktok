
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str
    JWT_SECRET: str

    # TikTok
    TIKTOK_CLIENT_KEY: str
    TIKTOK_CLIENT_SECRET: str
    TIKTOK_REDIRECT_URI: str

    # YouTube (Google OAuth 2.0 Web application client)
    YOUTUBE_CLIENT_ID: str
    YOUTUBE_CLIENT_SECRET: str
    YOUTUBE_REDIRECT_URI: str
    YOUTUBE_UPLOAD_SCOPES: str = (
        "https://www.googleapis.com/auth/youtube.upload "
        "https://www.googleapis.com/auth/youtube.readonly"
    )

    # Groq (for title/description/tags generation)
    GROQ_API_KEY: str
    GROQ_MODEL: str = "llama-3.1-8b-instant"

    FRONTEND_URL: str = "http://localhost:5173"

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        # CORS allow-list. Include the local dev URL and the production
        # Render static-site URL (set FRONTEND_URL to that URL in prod).
        return [
            "http://localhost:5173",
            self.FRONTEND_URL,
            "https://boltreels-web.onrender.com",
        ]

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
