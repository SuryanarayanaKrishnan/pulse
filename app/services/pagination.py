import base64
import json
from datetime import datetime
from uuid import UUID

from app.core.errors import AppError


def encode_cursor(posted_at: datetime, id_: UUID) -> str:
    raw = json.dumps({"p": posted_at.isoformat(), "i": str(id_)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(data["p"]), UUID(data["i"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError("Invalid cursor", code="invalid_cursor") from exc
