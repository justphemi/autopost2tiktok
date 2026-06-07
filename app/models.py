
from pydantic import BaseModel, HttpUrl
from typing import Optional
from datetime import datetime

class JobSubmit(BaseModel):
    url: str
    platform: str = "tiktok"           # future-proofed
    tiktok_account_id: str             # which connected account to post to
    scheduled_time: Optional[datetime] = None  # None = post immediately

class JobResponse(BaseModel):
    id: str
    status: str
    url: str
    created_at: datetime
    scheduled_time: Optional[datetime]
    tiktok_post_url: Optional[str]
    error_message: Optional[str]
    title: Optional[str]
    tags: Optional[list[str]]

class TikTokConnectResponse(BaseModel):
    auth_url: str