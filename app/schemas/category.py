from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.category import CategoryKind
from app.schemas.common import ORMModel


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: CategoryKind | None = Field(
        default=None, description="Defaults to the parent's kind for sub-categories, else expense"
    )
    parent_id: UUID | None = Field(default=None, description="A top-level category (one level only)")
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)
    is_fixed: bool = False


class CategoryUpdate(BaseModel):
    """Custom categories: any field. Defaults: only `name` (null resets it) and `is_hidden`."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)
    is_fixed: bool | None = None
    is_hidden: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "CategoryUpdate":
        if not self.model_fields_set:
            raise ValueError("Nothing to update")
        return self


class CategoryOut(ORMModel):
    id: UUID
    user_id: UUID | None
    slug: str | None
    name: str = Field(description="Effective name (the user's rename for a default, if any)")
    default_name: str
    kind: CategoryKind
    parent_id: UUID | None
    icon: str | None
    color: str | None
    is_fixed: bool
    is_default: bool = Field(description="True for the shared default set, false for the user's own")
    is_hidden: bool
    sort_order: int
