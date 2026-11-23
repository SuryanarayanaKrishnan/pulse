from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel, Timestamps


class EmailConnectionOut(Timestamps):
    """Tokens are never exposed."""

    provider: str
    email: str
    status: str
    scopes: str | None
    last_synced_at: datetime | None
    last_error: str | None


class GmailStartOut(BaseModel):
    authorization_url: str


class GmailCallbackOut(ORMModel):
    status: str
    email: str
    connection_id: str
