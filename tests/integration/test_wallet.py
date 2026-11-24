"""Ingest tokens, wallet payloads and cross-channel dedupe (SMS + wallet tap + statement)."""

from uuid import UUID

from app.services.ingest import process_raw_ingest
from app.services.statements.processing import process_statement
from tests.integration.test_flows import mk_account
from tests.statement_fixtures import STYLES, Txn, build_card, render_pdf


async def tap(api, body, path="apple-shortcut", token=None, query=""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await api.c.post(f"/v1/ingest/{path}{query}", json=body, headers=headers)


async def test_token_lifecycle_and_auth(new_user):
    api = new_user()
    assert (await api.get("/v1/ingest/token")).json() == {
        "exists": False, "prefix": None, "created_at": None, "last_used_at": None,
    }  # fmt: skip
    t1 = (await api.post("/v1/ingest/token")).json()
    assert t1["token"].startswith("pls_") and t1["token"].startswith(t1["prefix"])
    st = (await api.get("/v1/ingest/token")).json()
    assert st["exists"] and st["prefix"] == t1["prefix"] and "token" not in st
    await mk_account(api, last4="4321")
    body = {
        "amount": "AED 45.50",
        "merchant": "CARREFOUR",
        "last4": "4321",
        "timestamp": "2026-03-01T10:00:00+04:00",
    }
    # no credentials at all
    assert (await tap(api, body)).status_code == 401
    assert (await tap(api, body, token="pls_wrong")).json()["error"]["code"] == "invalid_ingest_token"
    ok = await tap(api, body, token=t1["token"])
    assert ok.status_code == 202, ok.text
    # the token also works as ?token= and cannot reach normal endpoints
    assert (
        await tap(api, {**body, "timestamp": "2026-03-02T10:00:00+04:00"}, query=f"?token={t1['token']}")
    ).status_code == 202
    assert (await api.c.get("/v1/me", headers={"Authorization": f"Bearer {t1['token']}"})).status_code == 401
    assert (await api.get("/v1/ingest/token")).json()["last_used_at"] is not None
    # rotate: old token dies, new works
    t2 = (await api.post("/v1/ingest/token")).json()
    assert t2["token"] != t1["token"]
    assert (await tap(api, body, token=t1["token"])).status_code == 401
    assert (
        await tap(api, {**body, "timestamp": "2026-03-03T10:00:00+04:00"}, token=t2["token"])
    ).status_code == 202
    # revoke
    assert (await api.delete("/v1/ingest/token")).status_code == 204
    assert (await api.delete("/v1/ingest/token")).status_code == 404
    assert (await tap(api, body, token=t2["token"])).status_code == 401


async def test_token_is_stored_hashed_and_scoped_to_its_user(new_user, sessionmaker_):
    from sqlalchemy import select

    from app.models.integration import IngestToken

    a, b = new_user(), new_user()
    ta = (await a.post("/v1/ingest/token")).json()["token"]
    await mk_account(a, last4="1111")
    await mk_account(b, last4="1111")
    async with sessionmaker_() as s:
        rows = (await s.execute(select(IngestToken))).scalars().all()
        assert all(ta not in (r.token_hash, r.prefix) for r in rows) and all(
            len(r.token_hash) == 64 for r in rows
        )
    r = await tap(
        a, {"amount": 10, "merchant": "X", "last4": "1111", "timestamp": "2026-03-01T10:00:00Z"}, token=ta
    )
    assert r.status_code == 202
    assert len((await a.get("/v1/transactions")).json()["items"]) == 1
    assert (await b.get("/v1/transactions")).json()["items"] == []


async def test_wallet_payload_variants_and_channels(new_user):
    api = new_user()
    tok = (await api.post("/v1/ingest/token")).json()["token"]
    acc = await mk_account(api, type="credit_card", name="Apple Card", last4="9999", institution="ENBD")
    r = await tap(
        api,
        {
            "amount": "1,234.50",
            "merchant": "NOON.COM",
            "card": "apple card",
            "currency": "AED",
            "timestamp": "2026-03-01T10:00:00+04:00",
        },
        token=tok,
    )
    out = r.json()
    assert out["parse_status"] == "parsed" and out["transaction_id"] and out["merged_with_existing"] is False
    t = (await api.get(f"/v1/transactions/{out['transaction_id']}")).json()
    assert t["amount_minor"] == 123_450 and t["account_id"] == acc["id"] and t["source"] == "apple_shortcut"
    assert t["merchant_normalized"] == "Noon"
    # google / samsung channels, numeric + negative (refund) amounts
    g = await tap(
        api,
        {"amount": 20, "merchant": "SPINNEYS", "last4": "9999", "timestamp": "2026-03-02T10:00:00+04:00"},
        "google-pay",
        tok,
    )
    s = await tap(
        api,
        {"amount": -5.25, "merchant": "SPINNEYS", "last4": "9999", "timestamp": "2026-03-03T10:00:00+04:00"},
        "samsung-wallet",
        tok,
    )
    sources = {t["source"]: t for t in (await api.get("/v1/transactions")).json()["items"]}
    assert set(sources) == {"apple_shortcut", "google_wallet", "samsung_wallet"}
    assert (
        sources["samsung_wallet"]["direction"] == "credit"
        and sources["samsung_wallet"]["amount_minor"] == 525
    )
    assert g.status_code == s.status_code == 202
    # unknown card name: stored raw, needs_review (nothing lost)
    nr = await tap(
        api,
        {"amount": 5, "merchant": "X", "card": "Mystery", "timestamp": "2026-03-04T10:00:00+04:00"},
        token=tok,
    )
    assert nr.json()["parse_status"] == "needs_review" and nr.json()["transaction_id"] is None
    # validation: bad amount
    assert (await tap(api, {"amount": "free", "merchant": "X"}, token=tok)).status_code == 422
    assert (await tap(api, {"amount": 0, "merchant": "X"}, token=tok)).status_code == 422
    # legacy IngestRequest shape still accepted on the same endpoint
    legacy = await tap(
        api, {"raw_text": "Purchase of AED 9.00 at SPINNEYS on card ending 9999", "sender": "ENBD"}, token=tok
    )
    assert legacy.status_code == 202 and legacy.json()["parse_status"] in ("pending", "parsed")


async def test_exact_resend_is_a_duplicate(new_user):
    api = new_user()
    tok = (await api.post("/v1/ingest/token")).json()["token"]
    await mk_account(api, last4="4321")
    body = {"amount": 10, "merchant": "X", "last4": "4321", "timestamp": "2026-03-01T10:00:00+04:00"}
    first = (await tap(api, body, token=tok)).json()
    again = await tap(api, body, token=tok)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["transaction_id"] == first["transaction_id"]
    assert len((await api.get("/v1/transactions")).json()["items"]) == 1


async def test_sms_and_wallet_tap_are_one_purchase(new_user, sessionmaker_):
    api = new_user()
    tok = (await api.post("/v1/ingest/token")).json()["token"]
    await mk_account(api, type="credit_card", institution="Emirates NBD", last4="1234", name="ENBD Card")
    sms = (
        await api.post(
            "/v1/ingest/sms",
            {
                "raw_text": "Purchase of AED 45.00 with Credit Card ending 1234 at CARREFOUR, DUBAI. Avl Cr. limit is AED 20,000.00",
                "sender": "ENBD",
                "received_at": "2026-03-01T10:02:00+04:00",
            },
        )
    ).json()
    async with sessionmaker_() as s:
        await process_raw_ingest(s, UUID(sms["id"]))
    r = (
        await tap(
            api,
            {
                "amount": 45,
                "merchant": "CARREFOUR MOE",
                "last4": "1234",
                "timestamp": "2026-03-01T10:00:30+04:00",
            },
            token=tok,
        )
    ).json()
    assert r["merged_with_existing"] is True and r["transaction_id"]
    items = (await api.get("/v1/transactions")).json()["items"]
    assert len(items) == 1 and items[0]["id"] == r["transaction_id"]
    # a *second* identical coffee from the same channel an hour later stays a separate purchase
    r2 = (
        await tap(
            api,
            {
                "amount": 45,
                "merchant": "CARREFOUR MOE",
                "last4": "1234",
                "timestamp": "2026-03-01T16:00:00+04:00",
            },
            token=tok,
        )
    ).json()
    assert r2["merged_with_existing"] is False
    assert len((await api.get("/v1/transactions")).json()["items"]) == 2


async def test_statement_sweep_upgrades_and_locks_the_captured_transaction(
    new_user, storage, encryptor, sessionmaker_
):
    api = new_user()
    tok = (await api.post("/v1/ingest/token")).json()["token"]
    await mk_account(api, type="credit_card", institution="Emirates NBD", last4="1234", name="ENBD Card")
    # tapped (wallet) + SMS for the same purchase, then the monthly statement arrives
    t = (
        await tap(
            api,
            {
                "amount": 120.50,
                "merchant": "CARREFOUR",
                "last4": "1234",
                "timestamp": "2026-03-02T09:00:00+04:00",
            },
            token=tok,
        )
    ).json()
    sms = (
        await api.post(
            "/v1/ingest/sms",
            {
                "raw_text": "Purchase of AED 120.50 with Credit Card ending 1234 at CARREFOUR, DUBAI.",
                "sender": "ENBD",
                "received_at": "2026-03-02T09:03:00+04:00",
            },
        )
    ).json()
    async with sessionmaker_() as s:
        await process_raw_ingest(s, UUID(sms["id"]))
    only = (await api.get("/v1/transactions")).json()["items"]
    assert len(only) == 1 and only[0]["id"] == t["transaction_id"]

    built = build_card(
        STYLES["enbd"], [Txn(2, "CARREFOUR DUBAI AE", 12050), Txn(10, "TALABAT DUBAI AE", 8525)]
    )
    up = await api.upload("/v1/statements/upload", render_pdf(built.lines), "march.pdf")
    async with sessionmaker_() as s:
        assert await process_statement(s, UUID(up.json()["id"]), storage, encryptor) == "parsed"
    items = (await api.get("/v1/transactions")).json()["items"]
    assert len(items) == 2  # the statement linked, it did not duplicate
    carrefour = next(i for i in items if i["merchant_normalized"] == "Carrefour")
    assert carrefour["id"] == t["transaction_id"] and carrefour["statement_id"] == up.json()["id"]
    assert carrefour["statement_locked"] is True and carrefour["amount_minor"] == 12050
    assert set(carrefour["sources"]) == {"apple_shortcut", "sms", "statement"}
    talabat = next(i for i in items if i["merchant_normalized"] == "Talabat")
    assert talabat["sources"] == ["statement"] and talabat["statement_locked"] is True
