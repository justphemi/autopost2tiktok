
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routes import health, auth, tiktok, tiktok_direct, youtube

app = FastAPI(title="boltreels API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(tiktok.router)
app.include_router(tiktok_direct.router)
app.include_router(youtube.router)

@app.get("/")
def root():
    return {"message": "boltreels API is running"}