from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Currency, ORMModel


class MeOut(ORMModel):
    id: UUID
    email: str | None
    display_name: str | None
    default_currency: str
    phone: str | None = Field(default=None, validation_alias="phone_e164", description="E.164")
    aani_phone: str | None = Field(
        default=None,
        validation_alias="aani_phone_e164",
        description="Mobile registered with Aani (UAE); payees are paid here. Falls back to `phone`.",
    )
    phone_discoverable: bool = Field(
        default=True, description="Whether contacts holding your number can find you in Pulse"
    )

    @field_validator("phone_discoverable", mode="before")
    @classmethod
    def _default_on(cls, v: Any) -> Any:
        return True if v is None else v  # not-yet-flushed rows have no server default applied


class MeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    default_currency: Currency | None = None
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="Your mobile (05x... ok); empty/null clears",
    )
    aani_phone: str | None = Field(
        default=None, max_length=32, description="UAE mobile registered with Aani. Empty/null clears"
    )
    phone_discoverable: bool | None = None
