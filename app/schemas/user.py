from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Currency, ORMModel


class MeOut(ORMModel):
    id: UUID
    email: str | None
    display_name: str | None
    default_currency: str


class MeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    default_currency: Currency | None = None
