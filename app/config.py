
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str
    JWT_SECRET: str
    GROQ_API_KEY: str
    TIKTOK_CLIENT_KEY: str
    TIKTOK_CLIENT_SECRET: str
    TIKTOK_REDIRECT_URI: str
    REDIS_URL: str = "redis://localhost:6379/0"
    FRONTEND_URL: str = "http://localhost:5173"

    class Config:
        env_file = ".env"

settings = Settings()
