from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import DomainValidationError, NotFoundError
from app.models.asset import Asset, AssetKind, ValuationSource
from app.models.portfolio import JewelleryItem
from app.schemas.jewellery import (
    BackupBlobIn,
    JewelleryCreate,
    JewelleryOut,
    JewelleryUpdate,
    JewelleryValuationOut,
)
from app.services.valuations import pricing
from app.services.valuations.service import effective, load_quote_book, record_valuation, revalue_asset

router = APIRouter(prefix="/jewellery", tags=["jewellery"])


def fineness(item: JewelleryItem) -> Decimal:
    if item.purity is not None:
        return item.purity
    assert item.karat is not None
    return (Decimal(item.karat) / 24).quantize(Decimal("0.0001"))


def _purity_meta(item: JewelleryItem) -> str:
    """Exact karat string when no override, so 22k values as 22/24 rather than a rounded 0.9167."""
    if item.purity is not None:
        return str(item.purity)
    return f"{item.karat}k"


def _sync_asset(asset: Asset, item: JewelleryItem) -> None:
    asset.name = item.description
    asset.quantity, asset.unit = item.weight_grams, "g"
    asset.cost_basis_minor, asset.acquired_on = item.purchase_price_minor, item.purchase_date
    asset.valuation_source = ValuationSource.market
    asset.meta = {**(asset.meta or {}), "purity": _purity_meta(item), "karat": item.karat}


def _out(item: JewelleryItem, asset: Asset, usd_oz_aed_gram: Decimal | None) -> JewelleryOut:
    eff = effective(asset)
    market = eff.value_minor if eff.basis == "market" else None
    cost = item.purchase_price_minor
    gain = market - cost if market is not None and cost is not None else None
    return JewelleryOut(
        asset_id=asset.id,
        description=item.description,
        weight_grams=item.weight_grams,
        karat=item.karat,
        purity=item.purity,
        fineness=fineness(item),
        making_charges_minor=item.making_charges_minor,
        purchase_price_minor=cost,
        purchase_date=item.purchase_date,
        photo_keys=list(item.photo_keys or []),
        backup_blob=item.backup_blob,
        valuation=JewelleryValuationOut(
            market_value_minor=market,
            basis=eff.basis,
            per_gram_aed_minor=pricing.to_minor(usd_oz_aed_gram) if usd_oz_aed_gram is not None else None,
            last_updated=eff.last_updated,
            note=eff.note,
            purchase_price_minor=cost,
            gain_loss_minor=gain,
            gain_loss_pct=round(gain / cost * 100, 1) if gain is not None and cost else None,
            explanation=(
                "Market value is the gold's melt value at today's spot price. A purchase price that includes "
                "making charges and dealer margin will normally show a loss against it."
            ),
        ),
    )


async def _pair(session: SessionDep, asset_id: UUID, user_id: UUID) -> tuple[JewelleryItem, Asset]:
    row = (
        await session.execute(
            select(JewelleryItem, Asset)
            .join(Asset, Asset.id == JewelleryItem.asset_id)
            .where(
                JewelleryItem.asset_id == asset_id,
                JewelleryItem.user_id == user_id,
                Asset.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Jewellery item not found")
    return row[0], row[1]


async def _render(session: SessionDep, item: JewelleryItem, asset: Asset) -> JewelleryOut:
    book = await load_quote_book(session)
    per_gram = None
    if book.gold:
        per_gram = pricing.gold_aed_per_gram(book.gold[0], book.aed_per_usd, fineness(item))
    return _out(item, asset, per_gram)


@router.post("", response_model=JewelleryOut, status_code=201)
async def add_item(payload: JewelleryCreate, user: CurrentUser, session: SessionDep) -> JewelleryOut:
    """Adds the piece as a `gold_jewellery` asset valued from live gold (weight x purity x spot)."""
    asset = Asset(user_id=user.id, kind=AssetKind.gold_jewellery, name=payload.description, currency="AED")
    session.add(asset)
    await session.flush()
    item = JewelleryItem(user_id=user.id, asset_id=asset.id, **payload.model_dump())
    session.add(item)
    _sync_asset(asset, item)
    book = await load_quote_book(session)
    if revalue_asset(asset, book):
        await record_valuation(session, asset)
    await session.commit()
    await session.refresh(asset)
    await session.refresh(item)
    return await _render(session, item, asset)


@router.get("", response_model=list[JewelleryOut])
async def list_items(user: CurrentUser, session: SessionDep) -> list[JewelleryOut]:
    rows = (
        await session.execute(
            select(JewelleryItem, Asset)
            .join(Asset, Asset.id == JewelleryItem.asset_id)
            .where(JewelleryItem.user_id == user.id, Asset.deleted_at.is_(None))
            .order_by(JewelleryItem.created_at)
        )
    ).all()
    return [await _render(session, i, a) for i, a in rows]


@router.get("/{asset_id}", response_model=JewelleryOut)
async def get_item(asset_id: UUID, user: CurrentUser, session: SessionDep) -> JewelleryOut:
    item, asset = await _pair(session, asset_id, user.id)
    return await _render(session, item, asset)


@router.patch("/{asset_id}", response_model=JewelleryOut)
async def update_item(
    asset_id: UUID, payload: JewelleryUpdate, user: CurrentUser, session: SessionDep
) -> JewelleryOut:
    item, asset = await _pair(session, asset_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    for required in ("description", "weight_grams", "photo_keys"):
        if required in data and data[required] is None:
            data.pop(required)
    if data.get("karat") is not None and "purity" not in data:
        item.purity = None  # a new karat replaces an old fineness override
    for k, v in data.items():
        setattr(item, k, v)
    if item.karat is None and item.purity is None:
        raise DomainValidationError("Provide karat or purity", code="missing_purity")
    _sync_asset(asset, item)
    if revalue_asset(asset, await load_quote_book(session)):
        await record_valuation(session, asset)
    await session.commit()
    await session.refresh(asset)
    await session.refresh(item)
    return await _render(session, item, asset)


@router.put("/{asset_id}/backup", response_model=JewelleryOut)
async def set_backup(
    asset_id: UUID, payload: BackupBlobIn, user: CurrentUser, session: SessionDep
) -> JewelleryOut:
    """Record metadata of an encrypted photo backup held in storage. The server never sees the plaintext."""
    if not payload.encrypted:
        raise DomainValidationError("Only encrypted backups are tracked", code="backup_not_encrypted")
    item, asset = await _pair(session, asset_id, user.id)
    item.backup_blob = {**payload.model_dump(), "updated_at": datetime.now(UTC).isoformat()}
    await session.commit()
    await session.refresh(item)
    return await _render(session, item, asset)


@router.delete("/{asset_id}", status_code=204)
async def delete_item(asset_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    _, asset = await _pair(session, asset_id, user.id)
    asset.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)
