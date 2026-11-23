from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.services.phone import is_phone_hash


class ContactMatchIn(BaseModel):
    hashes: list[str] = Field(
        min_length=1,
        max_length=1000,
        description="SHA-256 (lower-case hex) of each contact's E.164 number including the '+'",
    )

    @field_validator("hashes")
    @classmethod
    def _valid_hashes(cls, v: list[str]) -> list[str]:
        out = list(dict.fromkeys(h.strip().lower() for h in v))
        if not all(is_phone_hash(h) for h in out):
            raise ValueError("every hash must be 64 hex characters (SHA-256)")
        return out


class ContactMatch(BaseModel):
    phone_hash: str
    user_id: UUID
    display_name: str | None


class ContactMatchOut(BaseModel):
    matches: list[ContactMatch]
