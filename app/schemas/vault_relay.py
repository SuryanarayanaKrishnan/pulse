from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.vault_relay import INTERVAL_CHOICES, MAX_FILE_CHARS


def _interval(v: int) -> int:
    if v not in INTERVAL_CHOICES:
        raise ValueError(f"interval_days must be one of {list(INTERVAL_CHOICES)}")
    return v


class RelayArm(BaseModel):
    sealed_file: str = Field(min_length=2, max_length=MAX_FILE_CHARS)
    claim_code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    interval_days: int = 90

    _check = field_validator("interval_days")(_interval)


class RelayInterval(BaseModel):
    interval_days: int

    _check = field_validator("interval_days")(_interval)


class RelayClaimIn(BaseModel):
    claim_code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RelayClaimOut(BaseModel):
    status: Literal["released", "pending", "not_yet", "wrong_code"]
    sealed_file: str | None = None
    release_at: datetime | None = None
    days_left: int | None = None


class RelayOut(BaseModel):
    nominee_id: str
    phase: Literal["ok", "soon", "overdue", "claim_pending", "released"]
    interval_days: int
    release_due_at: datetime
    claim_release_at: datetime | None = None
