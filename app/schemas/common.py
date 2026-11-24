from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
MinorAmount = Annotated[int, Field(ge=0, le=10**15, description="Integer minor units (fils)")]
PositiveMinor = Annotated[int, Field(gt=0, le=10**15)]
Tag = Annotated[str, Field(min_length=1, max_length=40)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class Timestamps(ORMModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
