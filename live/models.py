from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class LiveSession(BaseModel):
    live_id: str

    title: str
    description: Optional[str] = None

    host_id: str
    host_name: str

    location: Optional[str] = None

    status: str = "created"

    viewer_count: int = 0

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    started_at: Optional[datetime] = None

    ended_at: Optional[datetime] = None