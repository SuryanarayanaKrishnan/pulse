from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.statement import ParseStatus
from app.models.transaction import RawIngest, Transaction
from app.services.ingest import process_raw_ingest


async def mk_account(api, **over):
    body = {"type": "bank", "name": "Main", "institution": "ENBD", "last4": "4321", **over}
    r = await api.post("/v1/accounts", body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_me_provisions_user_and_updates(new_user):
    api = new_user()
    r = await api.get("/v1/me")
    assert r.status_code == 200 and r.json()["default_currency"] == "AED"
    r = await api.patch("/v1/me", {"display_name": "Sam"})
    assert r.json()["display_name"] == "Sam"


async def test_accounts_crud_and_tenant_isolation(new_user):
    a, b = new_user(), new_user()
    acc = await mk_account(a, type="credit_card", credit_limit_minor=2_000_000, statement_day=5, due_day=25)
    assert acc["currency"] == "AED" and acc["credit_limit_minor"] == 2_000_000
    assert (await a.patch(f"/v1/accounts/{acc['id']}", {"name": "Renamed"})).json()["name"] == "Renamed"
    # Other user cannot see / modify / delete it.
    assert (await b.get(f"/v1/accounts/{acc['id']}")).status_code == 404
    assert (await b.patch(f"/v1/accounts/{acc['id']}", {"name": "x"})).status_code == 404
    assert (await b.delete(f"/v1/accounts/{acc['id']}")).status_code == 404
    assert (await b.get("/v1/accounts")).json() == []
    assert (await a.delete(f"/v1/accounts/{acc['id']}")).status_code == 204
    assert (await a.get("/v1/accounts")).json() == []


async def test_transactions_filters_cursor_and_dedupe(new_user):
    api = new_user()
    acc = await mk_account(api)
    base = datetime(2026, 3, 1, 12, tzinfo=UTC)
    for i in range(5):
        r = await api.post(
            "/v1/transactions",
            {
                "account_id": acc["id"],
                "amount_minor": 1000 * (i + 1),
                "direction": "debit" if i % 2 == 0 else "credit",
                "merchant_raw": f"CARREFOUR {i}00{i}",
                "posted_at": (base + timedelta(days=i)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
    # same posted_at on two rows exercises the (posted_at, id) tiebreak
    for _ in range(2):
        await api.post(
            "/v1/transactions",
            {"account_id": acc["id"], "amount_minor": 1, "direction": "debit", "posted_at": base.isoformat()},
        )

    seen, cursor = [], None
    while True:
        url = "/v1/transactions?limit=3" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get(url)).json()
        seen += [t["id"] for t in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 7 and len(set(seen)) == 7

    only_credit = (await api.get("/v1/transactions?direction=credit")).json()["items"]
    assert len(only_credit) == 2
    ranged = (
        await api.get("/v1/transactions", params={"date_from": (base + timedelta(days=3)).isoformat()})
    ).json()
    assert len(ranged["items"]) == 2
    search = (await api.get("/v1/transactions?q=carrefour")).json()["items"]
    assert len(search) == 5 and search[0]["merchant_normalized"].startswith("Carrefour")
    assert (await api.get("/v1/transactions?cursor=garbage")).status_code == 400

    body = {
        "account_id": acc["id"],
        "amount_minor": 5,
        "direction": "debit",
        "posted_at": base.isoformat(),
        "external_hash": "client-key-0001",
    }
    assert (await api.post("/v1/transactions", body)).status_code == 201
    dup = await api.post("/v1/transactions", body)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_transaction"

    tid = seen[0]
    assert (await api.delete(f"/v1/transactions/{tid}")).status_code == 204
    assert (await api.get(f"/v1/transactions/{tid}")).status_code == 404


async def test_transaction_on_foreign_account_rejected(new_user):
    a, b = new_user(), new_user()
    acc = await mk_account(a)
    r = await b.post(
        "/v1/transactions",
        {
            "account_id": acc["id"],
            "amount_minor": 5,
            "direction": "debit",
            "posted_at": datetime.now(UTC).isoformat(),
        },
    )
    assert r.status_code == 404


async def test_budget_tracks_spend(new_user):
    api = new_user()
    acc = await mk_account(api)
    cats = (await api.get("/v1/categories")).json()
    groceries = next(c for c in cats if c["name"] == "Groceries")
    b = await api.post(
        "/v1/budgets",
        {"category_id": groceries["id"], "month": "2026-03-17", "limit_minor": 100_000},
    )
    assert b.status_code == 201 and b.json()["month"] == "2026-03-01"
    for amt, day in ((30_000, "2026-03-02"), (20_000, "2026-03-30"), (99_999, "2026-04-01")):
        await api.post(
            "/v1/transactions",
            {
                "account_id": acc["id"],
                "amount_minor": amt,
                "direction": "debit",
                "category_id": groceries["id"],
                "posted_at": f"{day}T10:00:00+00:00",
            },
        )
    got = (await api.get("/v1/budgets?month=2026-03-05")).json()
    assert got[0]["spent_minor"] == 50_000 and got[0]["remaining_minor"] == 50_000
    dup = await api.post(
        "/v1/budgets", {"category_id": groceries["id"], "month": "2026-03-01", "limit_minor": 1}
    )
    assert dup.status_code == 409


async def test_assets_liabilities_net_worth(new_user):
    api = new_user()
    await api.post(
        "/v1/assets",
        {
            "kind": "gold",
            "name": "Gold bars",
            "quantity": "100.5",
            "unit": "g",
            "cost_basis_minor": 2_000_000,
            "manual_value_minor": 2_400_000,
            "meta": {"purity": "24k"},
        },
    )
    a2 = (
        await api.post("/v1/assets", {"kind": "cash", "name": "Savings", "manual_value_minor": 1_000_000})
    ).json()
    await api.post(
        "/v1/assets", {"kind": "stock", "name": "AAPL", "currency": "USD", "manual_value_minor": 100_000}
    )
    await api.post("/v1/liabilities", {"kind": "personal_loan", "name": "Loan", "outstanding_minor": 500_000})
    nw = (await api.get("/v1/net-worth")).json()
    assert nw["total_assets_minor"] == 3_400_000 and nw["net_worth_minor"] == 2_900_000
    assert nw["unconverted_currencies"] == ["USD"]
    nw = (await api.get("/v1/net-worth?usd_rate=3.6725")).json()
    assert nw["total_assets_minor"] == 3_400_000 + 367_250 and nw["unconverted_currencies"] == []
    await api.delete(f"/v1/assets/{a2['id']}")
    assert (await api.get("/v1/net-worth")).json()["total_assets_minor"] == 2_400_000
    assert (await api.get("/v1/assets")).json()[0]["meta"] == {"purity": "24k"}


async def test_split_group_balances_and_settlement(new_user):
    me, friend_user, outsider = new_user(), new_user(), new_user()
    await friend_user.get("/v1/me")  # provision so it can be linked
    g = (
        await me.post(
            "/v1/splits/groups",
            {
                "name": "Trip",
                "members": [
                    {"display_name": "Friend", "user_id": str(friend_user.user_id)},
                    {"display_name": "Offline Omar"},
                ],
            },
        )
    ).json()
    m = {x["display_name"]: x["id"] for x in g["members"]}
    assert len(g["members"]) == 3
    mine = next(x["id"] for x in g["members"] if x["user_id"] == str(me.user_id))
    omar, friend = m["Offline Omar"], m["Friend"]

    # equal: me pays 100.00 for all three -> 33.34/33.33/33.33
    e1 = await me.post(
        f"/v1/splits/groups/{g['id']}/expenses",
        {
            "description": "Dinner",
            "amount_minor": 10_000,
            "paid_by_member_id": mine,
            "participants": [{"member_id": x} for x in (mine, friend, omar)],
        },
    )
    assert e1.status_code == 201 and sum(s["share_minor"] for s in e1.json()["shares"]) == 10_000
    # exact: friend pays 60.00; me 10, omar 50
    e2 = await friend_user.post(
        f"/v1/splits/groups/{g['id']}/expenses",
        {
            "description": "Taxi",
            "amount_minor": 6_000,
            "paid_by_member_id": friend,
            "split_type": "exact",
            "participants": [
                {"member_id": mine, "amount_minor": 1_000},
                {"member_id": omar, "amount_minor": 5_000},
            ],
        },
    )
    assert e2.status_code == 201
    # percent: omar pays 30.00, 50/25/25
    e3 = await me.post(
        f"/v1/splits/groups/{g['id']}/expenses",
        {
            "description": "Snacks",
            "amount_minor": 3_000,
            "paid_by_member_id": omar,
            "split_type": "percent",
            "participants": [
                {"member_id": omar, "percent": "50"},
                {"member_id": mine, "percent": "25"},
                {"member_id": friend, "percent": "25"},
            ],
        },
    )
    assert e3.status_code == 201

    bad = await me.post(
        f"/v1/splits/groups/{g['id']}/expenses",
        {
            "description": "x",
            "amount_minor": 100,
            "paid_by_member_id": mine,
            "split_type": "exact",
            "participants": [{"member_id": mine, "amount_minor": 99}],
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "shares_mismatch"

    bal = (await me.get(f"/v1/splits/groups/{g['id']}/balances")).json()
    nets = {b["member_id"]: b["net_minor"] for b in bal["balances"]}
    assert sum(nets.values()) == 0
    # me: +10000 -3334 -1000 -750 ; friend: +6000 -3333 -750 ; omar: +3000 -3333 -5000 -1500
    assert nets[mine] == 10_000 - 3_334 - 1_000 - 750
    assert nets[friend] == 6_000 - 3_333 - 750
    assert nets[omar] == 3_000 - 3_333 - 5_000 - 1_500
    assert len(bal["transfers"]) <= 2

    # Settle everything via suggested transfers -> all zero.
    for t in bal["transfers"]:
        r = await me.post(
            f"/v1/splits/groups/{g['id']}/settlements",
            {
                "from_member_id": t["from_member_id"],
                "to_member_id": t["to_member_id"],
                "amount_minor": t["amount_minor"],
            },
        )
        assert r.status_code == 201
    after = (await me.get(f"/v1/splits/groups/{g['id']}/balances")).json()
    assert all(b["net_minor"] == 0 for b in after["balances"]) and after["transfers"] == []

    # Non-members cannot see the group; deleting an expense changes balances.
    assert (await outsider.get(f"/v1/splits/groups/{g['id']}")).status_code == 404
    assert (await outsider.get(f"/v1/splits/groups/{g['id']}/balances")).status_code == 404
    assert (await friend_user.get(f"/v1/splits/groups/{g['id']}")).status_code == 200
    assert (await me.delete(f"/v1/splits/groups/{g['id']}/expenses/{e1.json()['id']}")).status_code == 204
    changed = (await me.get(f"/v1/splits/groups/{g['id']}/balances")).json()
    assert any(b["net_minor"] != 0 for b in changed["balances"])


async def test_ingest_dedupe_enqueue_and_processing(new_user, queue, sessionmaker_):
    api = new_user()
    acc = await mk_account(api, last4="4321", institution="ENBD")
    sms = {
        "raw_text": "Purchase of AED 123.45 at CARREFOUR MOE on card ending 4321. Avl bal AED 900",
        "sender": "ENBD",
        "received_at": "2026-03-05T08:00:00+00:00",
    }
    r1 = await api.post("/v1/ingest/sms", sms)
    assert r1.status_code == 202 and r1.json()["duplicate"] is False
    r2 = await api.post("/v1/ingest/sms", sms)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert r2.json()["id"] == r1.json()["id"]
    assert [j[0] for j in queue.jobs] == ["process_ingest"]

    async with sessionmaker_() as s:
        status = await process_raw_ingest(s, __import__("uuid").UUID(r1.json()["id"]))
    assert status is ParseStatus.parsed
    page = (await api.get("/v1/transactions")).json()["items"]
    assert len(page) == 1
    t = page[0]
    assert (t["amount_minor"], t["direction"], t["source"], t["account_id"]) == (
        12_345,
        "debit",
        "sms",
        acc["id"],
    )
    assert t["merchant_normalized"] == "Carrefour Moe"

    # OTPs are skipped; unknown card goes to needs_review (no transaction).
    otp = (await api.post("/v1/ingest/notification", {"raw_text": "Your OTP is 123456"})).json()
    unknown = (
        await api.post("/v1/ingest/apple-shortcut", {"raw_text": "Spent AED 5 at X on card ending 9999"})
    ).json()
    structured = (
        await api.post(
            "/v1/ingest/apple-shortcut",
            {
                "account_id": acc["id"],
                "external_id": "shortcut-1",
                "fields": {"amount_minor": 777, "direction": "credit", "merchant": "Refund Co"},
            },
        )
    ).json()
    async with sessionmaker_() as s:
        from uuid import UUID

        assert await process_raw_ingest(s, UUID(otp["id"])) is ParseStatus.skipped
        assert await process_raw_ingest(s, UUID(unknown["id"])) is ParseStatus.needs_review
        assert await process_raw_ingest(s, UUID(structured["id"])) is ParseStatus.parsed
        # idempotent re-run
        assert await process_raw_ingest(s, UUID(structured["id"])) is ParseStatus.parsed
        n = len(
            (await s.execute(select(Transaction).where(Transaction.user_id == api.user_id))).scalars().all()
        )
        raw = (await s.execute(select(RawIngest).where(RawIngest.user_id == api.user_id))).scalars().all()
    assert n == 2 and len(raw) == 4
