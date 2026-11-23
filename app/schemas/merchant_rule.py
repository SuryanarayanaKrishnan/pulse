from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Timestamps


class MerchantRuleCreate(BaseModel):
    merchant: str = Field(
        min_length=1, max_length=160, description="Merchant name; normalised before matching"
    )
    category_id: UUID
    apply_to_existing: bool = False


class MerchantRuleOut(Timestamps):
    user_id: UUID
    merchant_key: str | None
    category_id: UUID | None
