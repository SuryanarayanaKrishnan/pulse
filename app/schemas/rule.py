from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.merchant_rule import DEFAULT_RULE_PRIORITY
from app.models.transaction import Direction, TxnSource
from app.schemas.common import Tag, Timestamps
from app.schemas.transaction import TransactionOut
from app.services.rule_engine import check_regex

TextOp = Literal["contains", "equals", "regex"]


class _TextCondition(BaseModel):
    op: TextOp
    value: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _regex_is_safe(self) -> "_TextCondition":
        if self.op == "regex":
            check_regex(self.value)  # raises ValueError -> 422
        return self


class MerchantCondition(_TextCondition):
    """Matches the normalised merchant name or the raw descriptor (case-insensitive)."""

    field: Literal["merchant"]


class DescriptionCondition(_TextCondition):
    """Matches the raw description line (SMS/statement text) or the notes (case-insensitive)."""

    field: Literal["description"]


class AmountCondition(BaseModel):
    """Amount in minor units (fils), compared with the transaction amount (always positive)."""

    field: Literal["amount"]
    op: Literal["equals", "between", "gte", "lte"]
    value_minor: int | None = Field(default=None, ge=0, description="equals / gte / lte")
    min_minor: int | None = Field(default=None, ge=0, description="between (inclusive)")
    max_minor: int | None = Field(default=None, ge=0, description="between (inclusive)")

    @model_validator(mode="after")
    def _shape(self) -> "AmountCondition":
        if self.op == "between":
            if self.min_minor is None or self.max_minor is None or self.min_minor > self.max_minor:
                raise ValueError("between needs min_minor <= max_minor")
        elif self.value_minor is None:
            raise ValueError(f"{self.op} needs value_minor")
        return self


class DirectionCondition(BaseModel):
    field: Literal["direction"]
    value: Direction


class AccountCondition(BaseModel):
    """An account or card of the user."""

    field: Literal["account"]
    account_id: UUID


class SourceCondition(BaseModel):
    field: Literal["source"]
    value: TxnSource


Condition = Annotated[
    MerchantCondition
    | DescriptionCondition
    | AmountCondition
    | DirectionCondition
    | AccountCondition
    | SourceCondition,
    Field(discriminator="field"),
]


class RuleActions(BaseModel):
    category_id: UUID | None = Field(default=None, description="Set the category")
    rename_merchant: str | None = Field(default=None, min_length=1, max_length=160)
    mark_transfer: bool = Field(
        default=False, description="Treat as a transfer: Transfer category (unless one is set) + excluded"
    )
    exclude_from_budget: bool = False
    tags: list[Tag] = Field(default_factory=list, max_length=10)

    @field_validator("tags")
    @classmethod
    def _dedupe_tags(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(t.strip() for t in v if t.strip()))

    @property
    def has_action(self) -> bool:
        return bool(
            self.category_id
            or self.rename_merchant
            or self.mark_transfer
            or self.exclude_from_budget
            or self.tags
        )


class RuleBase(RuleActions):
    name: str | None = Field(default=None, max_length=120)
    priority: int = Field(
        default=DEFAULT_RULE_PRIORITY, ge=0, le=1_000_000, description="Lower runs first and wins"
    )
    is_active: bool = True
    conditions: list[Condition] = Field(min_length=1, max_length=12, description="All must match (AND)")


class RuleCreate(RuleBase):
    apply_to_existing: bool = Field(default=False, description="Also apply to past transactions now")

    @model_validator(mode="after")
    def _needs_action(self) -> "RuleCreate":
        if not self.has_action:
            raise ValueError("A rule needs at least one action")
        return self


class RuleUpdate(BaseModel):
    """Partial update; the merged rule must still have at least one condition and one action."""

    name: str | None = Field(default=None, max_length=120)
    priority: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None
    conditions: list[Condition] | None = Field(default=None, min_length=1, max_length=12)
    category_id: UUID | None = None
    rename_merchant: str | None = Field(default=None, min_length=1, max_length=160)
    mark_transfer: bool | None = None
    exclude_from_budget: bool | None = None
    tags: list[Tag] | None = Field(default=None, max_length=10)


class RuleOut(Timestamps):
    name: str | None
    priority: int
    is_active: bool
    conditions: list[dict[str, object]]
    category_id: UUID | None
    rename_merchant: str | None
    mark_transfer: bool
    exclude_from_budget: bool
    tags: list[str]
    applied_count: int | None = Field(default=None, description="Only on create with apply_to_existing")


class RuleReorder(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500, description="Rule ids, highest priority first")


class RulePreviewIn(RuleActions):
    conditions: list[Condition] = Field(min_length=1, max_length=12)
    only_uncategorised: bool = False
    date_from: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)


class RulePreviewItem(BaseModel):
    transaction: TransactionOut
    category_would_change: bool


class RulePreviewOut(BaseModel):
    matched_count: int
    scanned_count: int
    truncated: bool = Field(description="True when the history was longer than the scan cap")
    items: list[RulePreviewItem]


class RuleApplyIn(BaseModel):
    only_uncategorised: bool = False
    date_from: datetime | None = None


class RuleApplyOut(BaseModel):
    matched: int
    updated: int


class SuggestedRule(RuleBase):
    """Body for POST /v1/rules, offered after a manual re-categorisation ("Always do this?")."""

    matching_count: int = Field(description="Other transactions this rule would re-categorise")


class TransactionUpdateOut(TransactionOut):
    suggested_rule: SuggestedRule | None = Field(
        default=None, description="Present after a re-categorisation: POST it to /v1/rules to accept"
    )
