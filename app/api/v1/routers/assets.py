from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates, get_owned
from app.core.errors import DomainValidationError, NotFoundError
from app.models.account import Account
from app.models.asset import Asset, Liability
from app.schemas.asset import (
    AssetCreate,
    AssetOut,
    AssetUpdate,
    LiabilityCreate,
    LiabilityOut,
    LiabilityUpdate,
    NetWorthOut,
)
from app.services.networth import AssetInput, LiabilityInput, compute_net_worth

router = APIRouter(tags=["assets"])


def _out(asset: Asset) -> AssetOut:
    return AssetOut.model_validate(asset)


@router.get("/assets", response_model=list[AssetOut])
async def list_assets(user: CurrentUser, session: SessionDep) -> list[AssetOut]:
    rows = await session.execute(
        select(Asset).where(Asset.user_id == user.id, Asset.deleted_at.is_(None)).order_by(Asset.created_at)
    )
    return [_out(a) for a in rows.scalars()]


@router.post("/assets", response_model=AssetOut, status_code=201)
async def create_asset(payload: AssetCreate, user: CurrentUser, session: SessionDep) -> AssetOut:
    asset = Asset(user_id=user.id, **payload.model_dump())
    if asset.manual_value_minor is not None:
        asset.valued_at = datetime.now(UTC)
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return _out(asset)


@router.get("/assets/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: UUID, user: CurrentUser, session: SessionDep) -> AssetOut:
    return _out(await get_owned(session, Asset, asset_id, user.id))


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: UUID, payload: AssetUpdate, user: CurrentUser, session: SessionDep
) -> AssetOut:
    asset = await get_owned(session, Asset, asset_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    if "meta" in data and data["meta"] is None:
        data.pop("meta")
    apply_updates(asset, data)
    if "manual_value_minor" in data:
        asset.valued_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(asset)
    return _out(asset)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    asset = await get_owned(session, Asset, asset_id, user.id)
    asset.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.get("/liabilities", response_model=list[LiabilityOut])
async def list_liabilities(user: CurrentUser, session: SessionDep) -> list[Liability]:
    rows = await session.execute(
        select(Liability)
        .where(Liability.user_id == user.id, Liability.deleted_at.is_(None))
        .order_by(Liability.created_at)
    )
    return list(rows.scalars())


@router.post("/liabilities", response_model=LiabilityOut, status_code=201)
async def create_liability(payload: LiabilityCreate, user: CurrentUser, session: SessionDep) -> Liability:
    if payload.account_id:
        try:
            await get_owned(session, Account, payload.account_id, user.id)
        except NotFoundError as exc:
            raise DomainValidationError("Unknown account", code="invalid_account") from exc
    liability = Liability(user_id=user.id, **payload.model_dump())
    session.add(liability)
    await session.commit()
    await session.refresh(liability)
    return liability


@router.get("/liabilities/{liability_id}", response_model=LiabilityOut)
async def get_liability(liability_id: UUID, user: CurrentUser, session: SessionDep) -> Liability:
    return await get_owned(session, Liability, liability_id, user.id)


@router.patch("/liabilities/{liability_id}", response_model=LiabilityOut)
async def update_liability(
    liability_id: UUID, payload: LiabilityUpdate, user: CurrentUser, session: SessionDep
) -> Liability:
    liability = await get_owned(session, Liability, liability_id, user.id)
    apply_updates(liability, payload.model_dump(exclude_unset=True))
    await session.commit()
    await session.refresh(liability)
    return liability


@router.delete("/liabilities/{liability_id}", status_code=204)
async def delete_liability(liability_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    liability = await get_owned(session, Liability, liability_id, user.id)
    liability.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.get("/net-worth", response_model=NetWorthOut)
async def net_worth(
    user: CurrentUser,
    session: SessionDep,
    usd_rate: Annotated[
        Decimal | None, Query(gt=0, description="AED per 1 USD; omit to exclude USD items")
    ] = None,
) -> NetWorthOut:
    assets = (
        await session.execute(select(Asset).where(Asset.user_id == user.id, Asset.deleted_at.is_(None)))
    ).scalars()
    liabilities = (
        await session.execute(
            select(Liability).where(Liability.user_id == user.id, Liability.deleted_at.is_(None))
        )
    ).scalars()
    base = user.default_currency
    result = compute_net_worth(
        [AssetInput(a.kind.value, a.currency, a.manual_value_minor, a.cost_basis_minor) for a in assets],
        [LiabilityInput(x.kind.value, x.currency, x.outstanding_minor) for x in liabilities],
        base_currency=base,
        fx_rates={"USD": usd_rate} if usd_rate and base != "USD" else None,
    )
    return NetWorthOut(
        base_currency=result.base_currency,
        total_assets_minor=result.total_assets_minor,
        total_liabilities_minor=result.total_liabilities_minor,
        net_worth_minor=result.net_worth_minor,
        assets_by_kind=result.assets_by_kind,
        liabilities_by_kind=result.liabilities_by_kind,
        unconverted_currencies=result.unconverted_currencies,
    )
