from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.schemas.market import FxQuoteOut, GoldQuoteOut, IndexQuoteOut, MarketQuotesOut
from app.services.valuations import pricing
from app.services.valuations.service import load_quote_book

router = APIRouter(prefix="/market", tags=["market"])


def _status(fetched_at: datetime | None, now: datetime) -> str:
    if fetched_at is None:
        return "unavailable"
    return "live" if now - fetched_at <= pricing.LIVE_WITHIN else "stale"


@router.get("/quotes", response_model=MarketQuotesOut)
async def quotes(user: CurrentUser, session: SessionDep) -> MarketQuotesOut:
    """Cached gold, FX and property-index data behind asset valuations, with how fresh each is."""
    book = await load_quote_book(session)
    now = datetime.now(UTC)
    if book.gold:
        usd_oz, at, source = book.gold
        per_gram = {
            f"{k}k": pricing.gold_aed_per_gram(usd_oz, book.aed_per_usd, Decimal(k) / 24).quantize(
                Decimal("0.01")
            )
            for k in (24, 22, 21, 18)
        }
        gold = GoldQuoteOut(
            status=_status(at, now), usd_per_oz=usd_oz, aed_per_gram=per_gram, last_updated=at, source=source
        )
    else:
        gold = GoldQuoteOut(
            status="unavailable", usd_per_oz=None, aed_per_gram={}, last_updated=None, source=None
        )
    fx = [
        FxQuoteOut(currency=c, aed_per_unit=v[0], status=_status(v[1], now), last_updated=v[1], source=v[2])
        for c, v in sorted(book.fx.items())
    ]
    if "USD" not in book.fx:
        fx.insert(
            0,
            FxQuoteOut(
                currency="USD",
                aed_per_unit=pricing.AED_PER_USD_PEG,
                status="peg",
                last_updated=None,
                source="peg",
            ),
        )
    indices = [
        IndexQuoteOut(key=k, latest_value=max(pts)[1], index_date=max(pts)[0], last_updated=fetched)
        for k, (pts, fetched) in sorted(book.indices.items())
    ]
    return MarketQuotesOut(gold=gold, fx=fx, property_indices=indices)
