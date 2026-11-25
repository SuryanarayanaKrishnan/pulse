from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models.vault_relay import VaultRelay
from app.services.vault_relay import (
    GRACE_HOURS,
    Phase,
    claim_release_at,
    days_left,
    phase_of,
    release_due_at,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def relay(**kw) -> VaultRelay:
    base = dict(
        user_id=uuid4(), nominee_id="n1", sealed_file="{}", claim_hash="a" * 64,
        interval_days=90, last_heartbeat_at=T0, claim_requested_at=None, warned_stage=0, released_at=None,
    )  # fmt: skip
    return VaultRelay(**{**base, **kw})


def test_phases_follow_the_clock() -> None:
    r = relay()
    assert phase_of(r, T0 + timedelta(days=10)) is Phase.OK
    assert phase_of(r, T0 + timedelta(days=80)) is Phase.SOON
    assert phase_of(r, T0 + timedelta(days=90)) is Phase.OVERDUE
    r.claim_requested_at = T0 + timedelta(days=91)
    assert phase_of(r, T0 + timedelta(days=91)) is Phase.CLAIM_PENDING
    r.released_at = T0 + timedelta(days=95)
    assert phase_of(r, T0 + timedelta(days=96)) is Phase.RELEASED


def test_due_date_and_days_left() -> None:
    r = relay(interval_days=60)
    assert release_due_at(T0, 60) == T0 + timedelta(days=60)
    assert days_left(r, T0 + timedelta(days=59, hours=12)) == 1
    assert days_left(r, T0 + timedelta(days=70)) == 0


def test_claim_release_waits_out_the_grace_period() -> None:
    assert claim_release_at(T0) == T0 + timedelta(hours=GRACE_HOURS)
