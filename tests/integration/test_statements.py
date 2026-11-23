"""Statement upload -> parse -> reconcile -> link, against real Postgres."""

from uuid import UUID

import pytest

from app.models.transaction import TxnSource
from app.services.ingest import process_raw_ingest
from app.services.statements.processing import process_statement
from tests.integration.test_flows import mk_account
from tests.statement_fixtures import STYLES, Txn, build_account, build_card, render_pdf

pytestmark = pytest.mark.usefixtures("migrated_db")


async def run_pipeline(sessionmaker_, storage, encryptor, statement_id: str):
    async with sessionmaker_() as s:
        return await process_statement(s, UUID(statement_id), storage, encryptor)


async def upload_card(api, built=None, **fields):
    built = built or build_card(STYLES["enbd"])
    r = await api.upload("/v1/statements/upload", render_pdf(built.lines), "march.pdf", fields)
    return r, built


async def card_account(api):
    return await mk_account(
        api, type="credit_card", institution="Emirates NBD", last4="1234", name="ENBD Card"
    )


async def sms(api, sessionmaker_, text: str, received: str, sender="ENBD"):
    r = (
        await api.post("/v1/ingest/sms", {"raw_text": text, "sender": sender, "received_at": received})
    ).json()
    async with sessionmaker_() as s:
        await process_raw_ingest(s, UUID(r["id"]))
    return r


async def test_upload_process_creates_lines_transactions_and_reconciles(
    new_user, queue, storage, encryptor, sessionmaker_
):
    api = new_user()
    acc = await card_account(api)
    r, built = await upload_card(api)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["duplicate"] is False and body["parse_status"] == "pending"
    assert [j[0] for j in queue.jobs] == ["process_statement"]

    assert await run_pipeline(sessionmaker_, storage, encryptor, body["id"]) == "parsed"
    st = (await api.get(f"/v1/statements/{body['id']}")).json()
    assert (
        st["parse_status"] == "parsed" and st["reconciliation_status"] == "matched" and st["diff_minor"] == 0
    )
    assert st["account_id"] == acc["id"] and st["institution"] == "enbd" and st["kind"] == "credit_card"
    assert (st["opening_balance_minor"], st["closing_balance_minor"]) == (built.opening, built.closing)
    assert st["period_start"] == "2026-02-25" and st["period_end"] == "2026-03-25"
    assert st["summary"]["min_due_minor"] == built.min_due and st["action_required"] is None
    assert "file_storage_key" not in st

    txns = (await api.get("/v1/transactions?limit=50")).json()["items"]
    assert len(txns) == len(built.txns) and {t["source"] for t in txns} == {"statement"}
    carrefour = next(t for t in txns if t["merchant_normalized"] == "Carrefour")
    assert carrefour["category_id"] is not None and carrefour["statement_id"] == body["id"]

    # same bytes again -> duplicate, nothing new queued
    r2, _ = await upload_card(api, built)
    assert r2.status_code == 200 and r2.json() == {**body, "duplicate": True, "parse_status": "parsed"}
    assert len(queue.jobs) == 1

    # reprocessing is idempotent
    assert await run_pipeline(sessionmaker_, storage, encryptor, body["id"]) == "parsed"
    assert len((await api.get("/v1/transactions?limit=50")).json()["items"]) == len(built.txns)

    rep = (await api.get(f"/v1/statements/{body['id']}/reconciliation")).json()
    assert rep["counts"] == {"lines": 7, "linked": 0, "statement_only": 7, "captured_only": 0}
    assert rep["balances"]["diff_minor"] == 0 and rep["reconciliation_status"] == "matched"


async def test_statement_lines_link_to_captured_sms_instead_of_duplicating(
    new_user, storage, encryptor, sessionmaker_
):
    api = new_user()
    await card_account(api)
    # Captured by SMS: two statement lines (one with a day of slack, merchant spelled differently)...
    await sms(
        api,
        sessionmaker_,
        "Purchase of AED 120.50 with Credit Card ending 1234 at CARREFOUR, DUBAI.",
        "2026-03-02T09:00:00+00:00",
    )
    await sms(
        api,
        sessionmaker_,
        "Your Credit Card ending 1234 was used for AED 85.25 at TALABAT on 12-Mar-26.",
        "2026-03-12T10:00:00+00:00",
    )
    # ...one the statement does not show, and one with the same amount but a wrong merchant/date.
    await sms(
        api,
        sessionmaker_,
        "Purchase of AED 77.00 with Credit Card ending 1234 at STARBUCKS, DUBAI.",
        "2026-03-20T09:00:00+00:00",
    )
    await sms(
        api,
        sessionmaker_,
        "Purchase of AED 150.00 with Credit Card ending 1234 at SOME OTHER SHOP, DUBAI.",
        "2026-03-30T09:00:00+00:00",
    )

    r, built = await upload_card(api)
    sid = r.json()["id"]
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "parsed"

    rep = (await api.get(f"/v1/statements/{sid}/reconciliation")).json()
    assert rep["counts"]["lines"] == 7 and rep["counts"]["linked"] == 2
    assert {p["transaction"]["source"] for p in rep["linked"]} == {"sms"}
    assert sorted(p["line"]["amount_minor"] for p in rep["linked"]) == [8525, 12050]
    assert rep["counts"]["statement_only"] == 5
    # captured but missing from the statement: the Starbucks SMS (in period). The 30 Mar SMS is outside it.
    assert [t["merchant_normalized"] for t in rep["captured_only"]] == ["Starbucks"]

    all_txns = (await api.get("/v1/transactions?limit=100")).json()["items"]
    assert len(all_txns) == 4 + 5  # 4 SMS + 5 statement-only lines; the 2 linked lines created nothing
    linked_ids = {p["transaction"]["id"] for p in rep["linked"]}
    assert all(t["statement_id"] == sid for t in all_txns if t["id"] in linked_ids)

    # reprocess gives the same picture
    await run_pipeline(sessionmaker_, storage, encryptor, sid)
    rep2 = (await api.get(f"/v1/statements/{sid}/reconciliation")).json()
    assert rep2["counts"] == rep["counts"]
    assert len((await api.get("/v1/transactions?limit=100")).json()["items"]) == 9


async def test_sms_arriving_after_the_statement_links_instead_of_duplicating(
    new_user, storage, encryptor, sessionmaker_
):
    api = new_user()
    await card_account(api)
    r, _ = await upload_card(api)
    await run_pipeline(sessionmaker_, storage, encryptor, r.json()["id"])
    before = len((await api.get("/v1/transactions?limit=100")).json()["items"])
    ing = await sms(
        api,
        sessionmaker_,
        "Purchase of AED 120.50 with Credit Card ending 1234 at CARREFOUR, DUBAI.",
        "2026-03-03T09:00:00+00:00",
    )
    assert len((await api.get("/v1/transactions?limit=100")).json()["items"]) == before
    # a second identical SMS is a genuinely new purchase: the statement line is already claimed
    await sms(
        api,
        sessionmaker_,
        "Purchase of AED 120.50 with Credit Card ending 1234 at CARREFOUR, DUBAI.",
        "2026-03-03T09:05:00+00:00",
    )
    assert len((await api.get("/v1/transactions?limit=100")).json()["items"]) == before + 1
    assert ing["duplicate"] is False


async def test_balance_mismatch_is_flagged(new_user, storage, encryptor, sessionmaker_):
    api = new_user()
    await card_account(api)
    built = build_card(STYLES["enbd"])
    lines = [ln.replace("120.50", "121.50") if ln.startswith("02 Mar") else ln for ln in built.lines]
    r = await api.upload("/v1/statements/upload", render_pdf(lines), "bad.pdf")
    await run_pipeline(sessionmaker_, storage, encryptor, r.json()["id"])
    st = (await api.get(f"/v1/statements/{r.json()['id']}")).json()
    assert st["reconciliation_status"] == "mismatch" and st["diff_minor"] == -100
    rep = (await api.get(f"/v1/statements/{r.json()['id']}/reconciliation")).json()
    assert rep["balances"]["diff_minor"] == -100
    assert rep["balances"]["computed_closing_minor"] == built.closing + 100


async def test_password_protected_pdf_flow(new_user, queue, storage, encryptor, sessionmaker_):
    api = new_user()
    await card_account(api)
    built = build_card(STYLES["enbd"])
    data = render_pdf(built.lines, password="Pass#123")
    r = await api.upload("/v1/statements/upload", data, "locked.pdf", {"institution": "Emirates NBD"})
    sid = r.json()["id"]
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "needs_password"
    st = (await api.get(f"/v1/statements/{sid}")).json()
    assert st["parse_status"] == "needs_password" and st["action_required"] == "set_statement_password"
    assert st["institution"] == "enbd" and st["parse_error"]

    # set a wrong password: statement is retried and stays locked, with a different message
    queue.jobs.clear()
    resp = await api.put("/v1/statement-passwords", {"institution": "enbd", "password": "wrong"})
    assert resp.status_code == 200
    assert "wrong" not in resp.text and "password" not in resp.json()
    assert [j[0] for j in queue.jobs] == ["process_statement"]
    assert (await api.get(f"/v1/statements/{sid}")).json()["parse_status"] == "pending"
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "needs_password"
    assert "did not open" in (await api.get(f"/v1/statements/{sid}")).json()["parse_error"]

    # correct password via PUT (replace) re-queues; never echoed back; stored encrypted
    queue.jobs.clear()
    ok = await api.put("/v1/statement-passwords", {"institution": "ENBD", "password": "Pass#123"})
    assert ok.status_code == 200 and "Pass#123" not in ok.text
    assert [j[0] for j in queue.jobs] == ["process_statement"]
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "parsed"
    assert (await api.get(f"/v1/statements/{sid}")).json()["reconciliation_status"] == "matched"

    listing = await api.get("/v1/statement-passwords")
    assert listing.json()[0]["institution"] == "enbd" and "Pass#123" not in listing.text
    from sqlalchemy import select

    from app.models.integration import StatementPassword

    async with sessionmaker_() as s:
        stored = (
            await s.execute(select(StatementPassword).where(StatementPassword.user_id == api.user_id))
        ).scalar_one()
        assert (
            stored.encrypted_password != "Pass#123"
            and encryptor.decrypt(stored.encrypted_password) == "Pass#123"
        )

    # POST on an existing institution conflicts; unknown institution is rejected; delete works
    assert (
        await api.post("/v1/statement-passwords", {"institution": "enbd", "password": "x"})
    ).status_code == 409
    assert (
        await api.post("/v1/statement-passwords", {"institution": "nope bank", "password": "x"})
    ).status_code == 422
    assert (await api.delete("/v1/statement-passwords/enbd")).status_code == 204
    assert (await api.delete("/v1/statement-passwords/enbd")).status_code == 404
    assert (await api.get("/v1/statement-passwords")).json() == []


async def test_new_password_post_requeues_locked_statements(
    new_user, queue, storage, encryptor, sessionmaker_
):
    api = new_user()
    await card_account(api)
    data = render_pdf(build_card(STYLES["adcb"]).lines, password="pw")
    r = await api.upload("/v1/statements/upload", data, "adcb.pdf")  # institution unknown at upload time
    sid = r.json()["id"]
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "needs_password"
    created = await api.post("/v1/statement-passwords", {"institution": "adcb", "password": "pw"})
    assert created.status_code == 201 and "password" not in created.json()
    assert queue.jobs[-1][0] == "process_statement"
    # without an account for ADCB the statement still parses but needs a review step
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "needs_review"
    st = (await api.get(f"/v1/statements/{sid}")).json()
    assert (
        st["institution"] == "adcb" and st["action_required"] == "choose_account" and st["account_id"] is None
    )


async def test_needs_review_then_assign_account_and_reprocess(
    new_user, queue, storage, encryptor, sessionmaker_
):
    api = new_user()
    r, built = await upload_card(api)
    sid = r.json()["id"]
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "needs_review"
    assert (await api.get("/v1/transactions")).json()["items"] == []
    acc = await mk_account(api, type="credit_card", institution="Some Bank", last4="9999")
    queue.jobs.clear()
    resp = await api.post(f"/v1/statements/{sid}/reprocess", {"account_id": acc["id"]})
    assert resp.status_code == 202 and resp.json()["parse_status"] == "pending"
    assert queue.jobs and queue.jobs[0][0] == "process_statement"
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "parsed"
    assert len((await api.get("/v1/transactions?limit=100")).json()["items"]) == len(built.txns)


async def test_bank_account_statement_and_csv(new_user, storage, encryptor, sessionmaker_):
    api = new_user()
    acc = await mk_account(api, type="bank", institution="Mashreq", last4="0123", name="Mashreq Current")
    built = build_account(STYLES["mashreq"])
    r = await api.upload("/v1/statements/upload", render_pdf(built.lines), "acct.pdf")
    assert await run_pipeline(sessionmaker_, storage, encryptor, r.json()["id"]) == "parsed"
    st = (await api.get(f"/v1/statements/{r.json()['id']}")).json()
    assert (
        st["account_id"] == acc["id"]
        and st["kind"] == "bank_account"
        and st["reconciliation_status"] == "matched"
    )
    txns = (await api.get("/v1/transactions?limit=100")).json()["items"]
    salary = next(t for t in txns if t["amount_minor"] == 1_500_000)
    assert salary["direction"] == "credit"

    csv_bytes = b"Date,Description,Amount,Balance\n01/04/2026,POS TALABAT,-50.00,950.00\n02/04/2026,REFUND,10.00,960.00\n"
    r2 = await api.upload(
        "/v1/statements/upload", csv_bytes, "apr.csv", {"institution": "mashreq", "account_id": acc["id"]}
    )
    assert await run_pipeline(sessionmaker_, storage, encryptor, r2.json()["id"]) == "parsed"
    st2 = (await api.get(f"/v1/statements/{r2.json()['id']}")).json()
    assert st2["reconciliation_status"] == "matched" and (
        st2["opening_balance_minor"],
        st2["closing_balance_minor"],
    ) == (100000, 96000)


async def test_upload_validation_and_tenancy(new_user):
    a, b = new_user(), new_user()
    assert (await a.upload("/v1/statements/upload", b"\x00\x01binary", "x.bin")).status_code == 415
    assert (await a.upload("/v1/statements/upload", b"", "x.pdf")).status_code == 422
    pdf = render_pdf(["hello"])
    assert (await a.upload("/v1/statements/upload", pdf, "x.pdf", {"institution": "nope"})).status_code == 422
    missing_acc = await a.upload(
        "/v1/statements/upload", pdf, "x.pdf", {"account_id": "00000000-0000-0000-0000-000000000001"}
    )
    assert missing_acc.status_code == 404
    ok = await a.upload("/v1/statements/upload", pdf, "x.pdf")
    sid = ok.json()["id"]
    assert (await b.get(f"/v1/statements/{sid}")).status_code == 404
    assert (await b.get(f"/v1/statements/{sid}/reconciliation")).status_code == 404
    assert (await b.post(f"/v1/statements/{sid}/reprocess")).status_code == 404
    assert (await b.delete(f"/v1/statements/{sid}")).status_code == 404
    assert (await b.get("/v1/statements")).json() == []
    assert [s["id"] for s in (await a.get("/v1/statements")).json()] == [sid]
    # the same file uploaded by another user is not a duplicate of the first user's
    assert (await b.upload("/v1/statements/upload", pdf, "x.pdf")).status_code == 202


async def test_unparseable_pdf_fails_with_reason(new_user, storage, encryptor, sessionmaker_):
    api = new_user()
    r = await api.upload("/v1/statements/upload", render_pdf(["Just some prose", "nothing tabular"]), "x.pdf")
    assert await run_pipeline(sessionmaker_, storage, encryptor, r.json()["id"]) == "failed"
    st = (await api.get(f"/v1/statements/{r.json()['id']}")).json()
    assert st["parse_status"] == "failed" and "No transactions" in st["parse_error"]


async def test_delete_statement_removes_its_transactions_and_unlinks_captures(
    new_user, storage, encryptor, sessionmaker_
):
    api = new_user()
    await card_account(api)
    await sms(
        api,
        sessionmaker_,
        "Purchase of AED 120.50 with Credit Card ending 1234 at CARREFOUR, DUBAI.",
        "2026-03-02T09:00:00+00:00",
    )
    r, built = await upload_card(api)
    sid = r.json()["id"]
    await run_pipeline(sessionmaker_, storage, encryptor, sid)
    assert (await api.delete(f"/v1/statements/{sid}")).status_code == 204
    left = (await api.get("/v1/transactions?limit=100")).json()["items"]
    assert len(left) == 1 and left[0]["source"] == TxnSource.sms.value and left[0]["statement_id"] is None
    assert (await api.get(f"/v1/statements/{sid}")).status_code == 404
    # the same file can be uploaded again afterwards
    assert (await upload_card(api, built))[0].status_code == 202


async def test_overlapping_statements_do_not_duplicate_transactions(
    new_user, storage, encryptor, sessionmaker_
):
    api = new_user()
    await card_account(api)
    first = build_card(
        STYLES["enbd"], [Txn(2, "CARREFOUR DUBAI AE", 12050), Txn(5, "TALABAT DUBAI AE", 8525)], 0
    )
    second = build_card(
        STYLES["enbd"], [Txn(5, "TALABAT DUBAI AE", 8525), Txn(9, "ENOC STATION 1234", 15000)], 8525
    )
    for built in (first, second):
        r = await api.upload("/v1/statements/upload", render_pdf(built.lines), "s.pdf")
        await run_pipeline(sessionmaker_, storage, encryptor, r.json()["id"])
    txns = (await api.get("/v1/transactions?limit=100")).json()["items"]
    assert sorted(t["amount_minor"] for t in txns) == [8525, 12050, 15000]
