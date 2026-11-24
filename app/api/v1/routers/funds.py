from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates
from app.api.v1.routers.prices import ProvidersDep
from app.core.config import get_settings
from app.core.errors import DomainValidationError, NotFoundError
from app.models.portfolio import FundHolding, FundKind, FundTransaction, FundTxnKind
from app.schemas.funds import (
    CasImportOut,
    FundNavOut,
    FundSchemeOut,
    FundsOut,
    FundTxnIn,
    FundTxnOut,
    HoldingCreate,
    HoldingOut,
    HoldingUpdate,
    NavIn,
)
from app.schemas.prices import HistoryOut
from app.services import cas as cas_parser
from app.services import funds as svc
from app.services.statements.documents import (
    DocumentError,
    PasswordInvalid,
    PasswordRequired,
    decrypt_pdf,
    extract_pdf,
)
from app.services.valuations import prices
from app.services.valuations.providers import ProviderError
from app.services.valuations.service import _upsert, load_quote_book, nav_symbol

router = APIRouter(prefix="/funds", tags=["funds"])


async def _holding(session: SessionDep, holding_id: UUID, user_id: UUID) -> FundHolding:
    h = (
        await session.execute(
            select(FundHolding).where(
                FundHolding.id == holding_id, FundHolding.user_id == user_id, FundHolding.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if h is None:
        raise NotFoundError("Holding not found")
    return h


@router.get("/search", response_model=list[FundSchemeOut])
async def search_schemes(
    user: CurrentUser, providers: ProvidersDep, q: Annotated[str, Query(min_length=2, max_length=80)]
) -> list[FundSchemeOut]:
    """Search Indian mutual-fund schemes by name (mfapi.in, free, no key)."""
    if providers.funds is None:
        raise DomainValidationError("Fund search is disabled", code="funds_disabled")
    try:
        return [FundSchemeOut(scheme_code=s.code, name=s.name) for s in await providers.funds.search(q)]
    except ProviderError as exc:
        raise DomainValidationError(
            f"Fund search is unavailable right now: {exc}", code="funds_unavailable"
        ) from exc


@router.get("/schemes/{scheme_code}/latest", response_model=FundNavOut)
async def latest_nav(
    scheme_code: str, user: CurrentUser, session: SessionDep, providers: ProvidersDep
) -> FundNavOut:
    """Latest NAV straight from the source (and cached for valuations)."""
    if providers.funds is None:
        raise DomainValidationError("Fund data is disabled", code="funds_disabled")
    try:
        nav = await providers.funds.latest(scheme_code)
    except ProviderError as exc:
        raise DomainValidationError(f"NAV unavailable right now: {exc}", code="funds_unavailable") from exc
    await _upsert(
        session, nav_symbol(scheme_code), nav.nav_date, nav.nav, providers.funds.name, datetime.now(UTC)
    )
    await session.commit()
    return FundNavOut(scheme_code=nav.code, name=nav.name, nav_date=nav.nav_date, nav=nav.nav)


@router.get("/holdings", response_model=FundsOut)
async def list_holdings(user: CurrentUser, session: SessionDep) -> FundsOut:
    book = await load_quote_book(session)
    return svc.summarise([svc.holding_out(h, book) for h in await svc.user_holdings(session, user.id)])


@router.post("/holdings", response_model=HoldingOut, status_code=201)
async def add_holding(payload: HoldingCreate, user: CurrentUser, session: SessionDep) -> HoldingOut:
    data = payload.model_dump()
    manual_nav = data.pop("manual_nav")
    data["currency"] = data["currency"] or ("INR" if payload.kind is FundKind.india_mf else "AED")
    if payload.kind is FundKind.india_mf and data["currency"] != "INR":
        raise DomainValidationError("Indian mutual funds are valued in INR", code="invalid_currency")
    h = FundHolding(user_id=user.id, **data)
    if manual_nav is not None:
        h.manual_nav, h.manual_nav_at = manual_nav, datetime.now(UTC)
    session.add(h)
    await session.commit()
    await session.refresh(h)
    return svc.holding_out(h, await load_quote_book(session))


@router.get("/holdings/{holding_id}", response_model=HoldingOut)
async def get_holding(holding_id: UUID, user: CurrentUser, session: SessionDep) -> HoldingOut:
    return svc.holding_out(await _holding(session, holding_id, user.id), await load_quote_book(session))


@router.patch("/holdings/{holding_id}", response_model=HoldingOut)
async def update_holding(
    holding_id: UUID, payload: HoldingUpdate, user: CurrentUser, session: SessionDep
) -> HoldingOut:
    h = await _holding(session, holding_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    for required in ("name", "units"):
        if required in data and data[required] is None:
            data.pop(required)
    apply_updates(h, data)
    await session.commit()
    await session.refresh(h)
    return svc.holding_out(h, await load_quote_book(session))


@router.delete("/holdings/{holding_id}", status_code=204)
async def delete_holding(holding_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    h = await _holding(session, holding_id, user.id)
    h.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.post("/holdings/{holding_id}/nav", response_model=HoldingOut)
async def set_manual_nav(
    holding_id: UUID, payload: NavIn, user: CurrentUser, session: SessionDep
) -> HoldingOut:
    """Enter a NAV yourself (UAE and other funds, or when no live NAV exists)."""
    h = await _holding(session, holding_id, user.id)
    day = payload.as_of or datetime.now(UTC).date()
    h.manual_nav, h.manual_nav_at = payload.nav, datetime(day.year, day.month, day.day, tzinfo=UTC)
    await session.commit()
    await session.refresh(h)
    return svc.holding_out(h, await load_quote_book(session))


@router.get("/holdings/{holding_id}/history", response_model=HistoryOut)
async def nav_history(
    holding_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    providers: ProvidersDep,
    days: Annotated[int, Query(ge=1, le=1825)] = 365,
) -> HistoryOut:
    h = await _holding(session, holding_id, user.id)
    if not h.scheme_code:
        raise DomainValidationError("This holding has no scheme code", code="no_scheme_code")
    out = await prices.history(session, providers, f"nav:{h.scheme_code}", days)
    assert out is not None
    return out


@router.get("/holdings/{holding_id}/transactions", response_model=list[FundTxnOut])
async def holding_transactions(holding_id: UUID, user: CurrentUser, session: SessionDep) -> list[FundTxnOut]:
    await _holding(session, holding_id, user.id)
    rows = await session.execute(
        select(FundTransaction)
        .where(FundTransaction.holding_id == holding_id, FundTransaction.user_id == user.id)
        .order_by(FundTransaction.txn_date.desc(), FundTransaction.created_at.desc())
    )
    return [
        FundTxnOut(
            id=t.id,
            txn_date=t.txn_date,
            kind=t.kind,
            units=t.units,
            nav=t.nav,
            amount_minor=t.amount_minor,
            description=t.description,
            source=t.source,
        )
        for t in rows.scalars()
    ]


@router.post("/holdings/{holding_id}/transactions", response_model=FundTxnOut, status_code=201)
async def add_transaction(
    holding_id: UUID, payload: FundTxnIn, user: CurrentUser, session: SessionDep
) -> FundTxnOut:
    """Record a purchase/redemption by hand. Units and average cost on the holding are updated."""
    h = await _holding(session, holding_id, user.id)
    out_kinds = (FundTxnKind.redemption, FundTxnKind.switch_out)
    sign = -1 if payload.kind in out_kinds else 1
    units = abs(payload.units) * sign
    if payload.kind is FundTxnKind.dividend:
        units = Decimal(0)
    amount = payload.amount_minor
    if amount is None:
        if payload.nav is None:
            raise DomainValidationError("Provide amount_minor or nav", code="missing_amount")
        amount = svc._minor(abs(payload.units) * payload.nav)
    if h.units + units < 0:
        raise DomainValidationError(
            "That would sell more units than the holding has", code="insufficient_units"
        )
    if sign > 0 and units > 0:
        new_units = h.units + units
        paid = Decimal(amount) / 100
        old_cost = h.units * (h.avg_cost_per_unit or Decimal(0))
        if h.avg_cost_per_unit is not None or h.units == 0:
            h.avg_cost_per_unit = ((old_cost + paid) / new_units).quantize(Decimal("0.000001"))
    h.units += units
    t = FundTransaction(
        user_id=user.id, holding_id=h.id, txn_date=payload.txn_date, kind=payload.kind, units=units,
        nav=payload.nav, amount_minor=amount * sign if payload.kind is not FundTxnKind.dividend else amount,
        description=payload.description, source="manual",
        dedupe_hash=f"manual-{datetime.now(UTC).timestamp()}-{h.units}",
    )  # fmt: skip
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return FundTxnOut(
        id=t.id, txn_date=t.txn_date, kind=t.kind, units=t.units, nav=t.nav, amount_minor=t.amount_minor,
        description=t.description, source=t.source,
    )  # fmt: skip


@router.post("/cas/import", response_model=CasImportOut)
async def import_cas(
    user: CurrentUser,
    session: SessionDep,
    providers: ProvidersDep,
    file: Annotated[UploadFile, File()],
    password: Annotated[str | None, Form()] = None,
) -> CasImportOut:
    """Import a CAMS/KFintech CAS PDF. The PAN-based password you supply is used in memory only: neither
    it nor the file is stored or logged. Re-importing the same statement adds nothing twice."""
    data = await file.read(get_settings().max_upload_bytes + 1)
    if len(data) > get_settings().max_upload_bytes:
        raise DomainValidationError("File too large", code="file_too_large")
    try:
        clear = decrypt_pdf(data, [password] if password else [])
        text = extract_pdf(clear).text
    except PasswordRequired as exc:
        raise DomainValidationError(
            "This PDF is password protected: send the password", code="password_required"
        ) from exc
    except PasswordInvalid as exc:
        raise DomainValidationError("That password does not open the PDF", code="password_invalid") from exc
    except DocumentError as exc:
        raise DomainValidationError(f"Could not read the PDF: {exc}", code="unreadable_pdf") from exc
    result = cas_parser.parse_cas(text)
    if not result.holdings:
        raise DomainValidationError(result.warnings[0], code="not_a_cas")
    return await svc.import_cas(session, user.id, result, providers.funds, await load_quote_book(session))
