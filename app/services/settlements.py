"""Settlement state machine (Aani and friends).

Aani has no public third-party API, so Pulse cannot see the payment. The flow is therefore a handshake::

    payer: POST .../settlements/aani-request  -> status=pending           (nothing counts yet)
    payer pays in their bank app ("pay by mobile number")
    payer: POST .../settlements/{id}/mark-paid -> still pending, marked_paid_at set  (idempotent)
    payee: POST .../settlements/{id}/confirm   -> status=confirmed         (now counts towards balances)
    either side / creator: DELETE .../{id}      -> cancelled (only while pending)

The payee may confirm without a prior mark-paid (money arrived, payer forgot to tap). Settlements of
other methods (cash, bank_transfer, other) are recorded by a group member as already done and are
confirmed immediately, which keeps the pre-existing behaviour of ``POST .../settlements``.

A member without a Pulse account cannot act, so the settlement's creator acts for them.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError
from app.models.split import Settlement, SettlementMethod, SettlementStatus


@dataclass(frozen=True, slots=True)
class Parties:
    payer_user_id: UUID | None
    payee_user_id: UUID | None
    creator_id: UUID

    def payer_acts(self, user_id: UUID) -> bool:
        return user_id == self.payer_user_id or (self.payer_user_id is None and user_id == self.creator_id)

    def payee_acts(self, user_id: UUID) -> bool:
        return user_id == self.payee_user_id or (self.payee_user_id is None and user_id == self.creator_id)


def initial_status(method: SettlementMethod) -> SettlementStatus:
    return SettlementStatus.pending if method is SettlementMethod.aani else SettlementStatus.confirmed


def mark_paid(s: Settlement, parties: Parties, user_id: UUID, now: datetime) -> bool:
    """Payer reports that they paid. Returns False when it was already marked (idempotent)."""
    if not parties.payer_acts(user_id):
        raise ForbiddenError("Only the payer can mark this as paid", code="not_payer")
    if s.status is not SettlementStatus.pending:
        raise ConflictError("This settlement is already confirmed", code="settlement_not_pending")
    if s.marked_paid_at is not None:
        return False
    s.marked_paid_at, s.marked_paid_by = now, user_id
    return True


def confirm(s: Settlement, parties: Parties, user_id: UUID, now: datetime) -> None:
    """Payee confirms the money arrived; from here it counts towards balances."""
    if not parties.payee_acts(user_id):
        raise ForbiddenError("Only the person being paid can confirm", code="not_payee")
    if s.status is not SettlementStatus.pending:
        raise ConflictError("This settlement is already confirmed", code="settlement_not_pending")
    s.status = SettlementStatus.confirmed
    s.confirmed_at, s.confirmed_by = now, user_id
    s.settled_at = now


def may_cancel(s: Settlement, parties: Parties, user_id: UUID) -> None:
    if s.status is not SettlementStatus.pending:
        raise ConflictError("A confirmed settlement cannot be cancelled", code="settlement_not_pending")
    if not (parties.payer_acts(user_id) or parties.payee_acts(user_id) or user_id == s.created_by):
        raise ForbiddenError("Only the payer, the payee or the creator can cancel", code="not_party")


def allowed_actions(s: Settlement, parties: Parties, user_id: UUID) -> dict[str, bool]:
    pending = s.status is SettlementStatus.pending
    return {
        "can_mark_paid": pending and s.marked_paid_at is None and parties.payer_acts(user_id),
        "can_confirm": pending and parties.payee_acts(user_id),
        "can_cancel": pending
        and (parties.payer_acts(user_id) or parties.payee_acts(user_id) or user_id == s.created_by),
    }


def format_amount(amount_minor: int, currency: str) -> str:
    return f"{currency} {amount_minor // 100:,}.{amount_minor % 100:02d}"


def share_text(payee_name: str, amount_minor: int, currency: str, group_name: str, payee_phone: str) -> str:
    return (
        f'Hi {payee_name}, I\'m settling {format_amount(amount_minor, currency)} for "{group_name}" '
        f"via Aani to your mobile {payee_phone}. Please confirm in Pulse once it arrives."
    )
