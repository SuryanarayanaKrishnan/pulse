"""Salary detection across every capture path, plus GET /v1/salary."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.transaction import Direction, Transaction, TxnSource
from tests.integration.test_flows import mk_account
from tests.integration.test_statements import run_pipeline
from tests.statement_fixtures import STYLES, build_account, render_pdf


async def credit(api, account_id: str, desc: str | None, amount: int, when: str, **over):
    r = await api.post(
        "/v1/transactions",
        {
            "account_id": account_id,
            "amount_minor": amount,
            "direction": "credit",
            "merchant_raw": desc,
            "posted_at": f"{when}T09:00:00+00:00",
            **over,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_empty_summary(new_user):
    s = (await new_user().get("/v1/salary")).json()
    assert s["detected"] is False and s["records"] == [] and s["latest"] is None
    assert s["is_recurring"] is False and s["average_monthly_minor"] is None and s["monthly"] == []


async def test_manual_credits_build_a_recurring_summary(new_user):
    api = new_user()
    acc = await mk_account(api)
    await credit(api, acc["id"], "SALARY CREDIT FROM ACME LLC", 1_400_000, "2026-01-25")
    await credit(api, acc["id"], "WPS SALARY - ACME LLC", 1_500_000, "2026-02-26")
    await credit(api, acc["id"], "PAYROLL MARCH 2026 FROM ACME LLC", 1_600_000, "2026-03-25")
    await credit(api, acc["id"], "IPI TRANSFER FROM AHMED", 2_000_000, "2026-03-10")  # not a salary
    await credit(api, acc["id"], "SALARY ADVANCE", 500_000, "2026-03-11")  # excluded
    await api.post(  # a debit mentioning salary is not income
        "/v1/transactions",
        {
            "account_id": acc["id"],
            "amount_minor": 900_000,
            "direction": "debit",
            "merchant_raw": "SALARY CERTIFICATE",
            "posted_at": "2026-03-12T09:00:00+00:00",
        },
    )
    s = (await api.get("/v1/salary")).json()
    assert s["detected"] and s["is_recurring"] and s["currency"] == "AED" and s["months_observed"] == 3
    assert s["latest"]["amount_minor"] == 1_600_000 and s["latest"]["employer"] == "Acme LLC"
    assert s["latest"]["received_on"] == "2026-03-25" and s["latest"]["matched_by"] == "payroll"
    assert s["average_monthly_minor"] == 1_500_000
    assert s["latest_month_minor"] == 1_600_000 and s["previous_month_minor"] == 1_500_000
    assert s["change_minor"] == 100_000 and s["typical_day"] == 25
    assert [m["month"] for m in s["monthly"]] == ["2026-03-01", "2026-02-01", "2026-01-01"]
    assert [r["amount_minor"] for r in s["records"]] == [1_600_000, 1_500_000, 1_400_000]
    assert s["employers"] == [{"employer": "Acme LLC", "payments": 3, "latest_amount_minor": 1_600_000}]
    assert (await api.get("/v1/salary?months=2")).json()["monthly"][-1]["month"] == "2026-02-01"
    assert len((await api.get("/v1/salary?months=2")).json()["records"]) == 2
    assert (await api.get("/v1/salary?months=0")).status_code == 422


async def test_sms_salary_credit_is_recorded(new_user, sessionmaker_):
    from app.services.ingest import process_raw_ingest

    api = new_user()
    await mk_account(api, institution="Mashreq", last4="9876")
    r = (
        await api.post(
            "/v1/ingest/sms",
            {
                "raw_text": "Salary credited Dhs. 15000 to your account ending 9876",
                "sender": "MASHREQ",
                "received_at": "2026-03-25T08:00:00+04:00",
            },
        )
    ).json()
    async with sessionmaker_() as s:
        await process_raw_ingest(s, UUID(r["id"]))
    s = (await api.get("/v1/salary")).json()
    assert (
        s["detected"]
        and s["latest"]["amount_minor"] == 1_500_000
        and s["latest"]["received_on"] == "2026-03-25"
    )
    assert s["latest"]["employer"] is None
    txns = (await api.get("/v1/transactions")).json()["items"]
    assert txns[0]["category_id"] == next(  # filed under the Salary default
        c["id"] for c in (await api.get("/v1/categories")).json() if c["slug"] == "salary"
    )
    # a non-salary credit SMS is ignored
    r2 = (
        await api.post(
            "/v1/ingest/sms",
            {"raw_text": "AED 5,000.00 has been credited to your account ending 9876", "sender": "MASHREQ"},
        )
    ).json()
    async with sessionmaker_() as sess:
        await process_raw_ingest(sess, UUID(r2["id"]))
    assert len((await api.get("/v1/salary")).json()["records"]) == 1


async def test_statement_line_is_recorded_once_even_when_reprocessed(
    new_user, storage, encryptor, sessionmaker_
):
    api = new_user()
    await mk_account(api, type="bank", institution="Mashreq", last4="0123", name="Mashreq Current")
    built = build_account(STYLES["mashreq"])
    r = await api.upload("/v1/statements/upload", render_pdf(built.lines), "acct.pdf")
    sid = r.json()["id"]
    assert await run_pipeline(sessionmaker_, storage, encryptor, sid) == "parsed"
    s = (await api.get("/v1/salary")).json()
    assert len(s["records"]) == 1 and s["records"][0]["amount_minor"] == 1_500_000
    assert s["records"][0]["employer"] == "Acme LLC" and s["records"][0]["received_on"] == "2026-03-03"
    await api.post(f"/v1/statements/{sid}/reprocess")
    await run_pipeline(sessionmaker_, storage, encryptor, sid)
    assert len((await api.get("/v1/salary")).json()["records"]) == 1
    # removing the statement removes its transactions and with them the detected salary
    assert (await api.delete(f"/v1/statements/{sid}")).status_code == 204
    assert (await api.get("/v1/salary")).json()["detected"] is False


async def test_rescan_dismiss_and_delete(new_user, sessionmaker_):
    api = new_user()
    acc = await mk_account(api)
    # a credit that predates detection (inserted directly, bypassing the capture paths)
    async with sessionmaker_() as s:
        s.add(
            Transaction(
                user_id=api.user_id,
                account_id=UUID(acc["id"]),
                amount_minor=1_200_000,
                currency="AED",
                direction=Direction.credit,
                merchant_raw="SALARY JAN 2026 BETA FZ-LLC",
                posted_at=datetime(2026, 1, 28, 9, tzinfo=UTC),
                source=TxnSource.manual,
            )
        )
        await s.commit()
    assert (await api.get("/v1/salary")).json()["detected"] is False
    assert (await api.post("/v1/salary/detect")).json() == {"scanned": 1, "detected": 1}
    assert (await api.post("/v1/salary/detect")).json() == {"scanned": 1, "detected": 0}  # idempotent
    rec = (await api.get("/v1/salary")).json()["records"][0]
    assert (
        rec["employer"] == "Beta FZ-LLC"
        or rec["employer"] == "Beta FZ LLC"
        or rec["employer"].startswith("Beta")
    )

    # dismissing a false positive hides it and a re-scan does not bring it back
    assert (await api.delete(f"/v1/salary/records/{rec['id']}")).status_code == 204
    assert (await api.get("/v1/salary")).json()["detected"] is False
    assert (await api.post("/v1/salary/detect")).json()["detected"] == 0
    assert (await api.get("/v1/salary")).json()["detected"] is False
    assert (await api.delete(f"/v1/salary/records/{rec['id']}")).status_code == 404

    # deleting the transaction drops its record
    t = await credit(api, acc["id"], "SALARY ACME LLC", 1_000_000, "2026-02-25")
    assert (await api.get("/v1/salary")).json()["detected"] is True
    assert (await api.delete(f"/v1/transactions/{t['id']}")).status_code == 204
    assert (await api.get("/v1/salary")).json()["detected"] is False


async def test_salary_is_tenant_scoped(new_user):
    a, b = new_user(), new_user()
    acc = await mk_account(a)
    await credit(a, acc["id"], "SALARY ACME LLC", 1_000_000, "2026-02-25")
    rec = (await a.get("/v1/salary")).json()["records"][0]
    assert (await b.get("/v1/salary")).json()["detected"] is False
    assert (await b.delete(f"/v1/salary/records/{rec['id']}")).status_code == 404
    assert (await b.post("/v1/salary/detect")).json() == {"scanned": 0, "detected": 0}
    assert (await a.get("/v1/salary")).json()["detected"] is True
