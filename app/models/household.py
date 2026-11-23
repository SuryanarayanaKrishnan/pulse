import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, pk_uuid, str_enum


class HouseholdRole(enum.StrEnum):
    owner = "owner"
    adult = "adult"
    dependent = "dependent"


class MemberStatus(enum.StrEnum):
    invited = "invited"
    active = "active"
    declined = "declined"
    removed = "removed"


class ShareLevel(enum.StrEnum):
    """What a member lets the rest of the household see of their own finances."""

    nothing = "nothing"
    totals = "totals"  # month income / spend / net and net worth, no breakdown
    categories = "categories"  # totals plus spend by category
    full = "full"  # categories plus their transactions


class Household(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "households"

    id: Mapped[uuid.UUID] = pk_uuid()
    name: Mapped[str] = mapped_column(String(120))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )


class HouseholdMember(Base, TimestampMixin):
    """A seat in a household. user_id stays NULL until an invite sent to a phone/email is accepted."""

    __tablename__ = "household_members"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id", name="uq_household_members_household_user"),
        Index("ix_household_members_user_id", "user_id"),
        Index("ix_household_members_invite_phone_hash", "invite_phone_hash"),
        Index(
            "uq_household_members_one_owner",
            "household_id",
            unique=True,
            postgresql_where=text("role = 'owner' AND status IN ('invited', 'active')"),
        ),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[HouseholdRole] = mapped_column(str_enum(HouseholdRole, "household_role"))
    status: Mapped[MemberStatus] = mapped_column(
        str_enum(MemberStatus, "household_member_status"), server_default="invited"
    )
    # Set by the member themselves; new members share nothing until they choose to.
    share_level: Mapped[ShareLevel] = mapped_column(
        str_enum(ShareLevel, "household_share_level"), server_default="nothing"
    )
    invite_phone_e164: Mapped[str | None] = mapped_column(String(16))
    invite_phone_hash: Mapped[str | None] = mapped_column(String(64))
    invite_email: Mapped[str | None] = mapped_column(String(320))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
