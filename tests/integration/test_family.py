"""Households: invites, roles, per-member sharing levels and the privacy-respecting overview."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.models.audit import AuditLog
from tests.integration.test_aani import e164, mobile
from tests.integration.test_flows import mk_account


async def setup_user(api, phone=None):
    body = {"phone": phone} if phone else {}
    r = await api.patch("/v1/me", body) if body else await api.get("/v1/me")
    assert r.status_code == 200, r.text


async def spend(api, account, amount, category=None, merchant="Shop", direction="debit"):
    body = {
        "account_id": account, "amount_minor": amount, "direction": direction,
        "posted_at": datetime.now(UTC).isoformat(), "merchant_normalized": merchant,
    }  # fmt: skip
    if category:
        body["category_id"] = next(
            c["id"] for c in (await api.get("/v1/categories")).json() if c["name"] == category
        )
    assert (await api.post("/v1/transactions", body)).status_code == 201


async def family(new_user):
    """Owner A, adult B (joined by phone) and dependent C (joined by email)."""
    a, b, c = new_user(), new_user(), new_user()
    pb = mobile()
    await setup_user(a)
    await setup_user(b, pb)
    await setup_user(c)
    h = (await a.post("/v1/households", {"name": "Home", "display_name": "Alex"})).json()
    ib = await a.post(
        f"/v1/households/{h['id']}/invitations", {"display_name": "Bea", "role": "adult", "phone": pb}
    )
    assert ib.status_code == 201, ib.text
    ic = await a.post(
        f"/v1/households/{h['id']}/invitations",
        {"display_name": "Cal", "role": "dependent", "email": f"{c.user_id.hex[:8]}@example.com"},
    )
    assert ic.status_code == 201, ic.text
    for who in (b, c):
        assert (await who.post(f"/v1/households/{h['id']}/invitations/accept")).status_code == 200
    return a, b, c, h["id"], pb


async def test_create_invite_accept_and_masking(new_user):
    a, b = new_user(), new_user()
    await setup_user(a)
    pb = mobile()
    await setup_user(b, pb)
    h = (await a.post("/v1/households", {"name": "Home"})).json()
    assert h["your_role"] == "owner" and h["members"][0]["share_level"] == "nothing"
    inv = (
        await a.post(
            f"/v1/households/{h['id']}/invitations", {"display_name": "Bea", "role": "adult", "phone": pb}
        )
    ).json()
    assert inv["status"] == "invited" and e164(pb)[-2:] in inv["invited_via"] and e164(pb) not in str(inv)
    assert (await b.get(f"/v1/households/{h['id']}")).status_code == 404  # not a member until accepted
    pending = (await b.get("/v1/households/invitations")).json()
    assert [p["household_name"] for p in pending] == ["Home"] and pending[0]["role"] == "adult"
    joined = (await b.post(f"/v1/households/{h['id']}/invitations/accept")).json()
    assert joined["your_role"] == "adult" and len(joined["members"]) == 2
    assert (await b.get("/v1/households/invitations")).json() == []
    assert [x["name"] for x in (await b.get("/v1/households")).json()] == ["Home"]
    # duplicates and bad input
    dup = await a.post(
        f"/v1/households/{h['id']}/invitations", {"display_name": "B2", "role": "adult", "phone": pb}
    )
    assert dup.status_code == 409
    assert (
        await a.post(
            f"/v1/households/{h['id']}/invitations", {"display_name": "X", "role": "adult", "phone": "12"}
        )
    ).status_code == 422
    both = {"display_name": "X", "role": "adult", "phone": pb, "email": "x@example.com"}
    assert (await a.post(f"/v1/households/{h['id']}/invitations", both)).status_code == 422
    assert (
        await a.post(
            f"/v1/households/{h['id']}/invitations",
            {"display_name": "X", "role": "owner", "email": "x@example.com"},
        )
    ).status_code == 422


async def test_invite_for_someone_without_an_account_is_claimed_by_their_phone(new_user):
    a, late = new_user(), new_user()
    await setup_user(a)
    p = mobile()
    h = (await a.post("/v1/households", {"name": "Home"})).json()
    inv = await a.post(
        f"/v1/households/{h['id']}/invitations", {"display_name": "Late", "role": "adult", "phone": p}
    )
    assert inv.status_code == 201
    await setup_user(late)  # signs up later, no phone yet: nothing to claim
    assert (await late.get("/v1/households/invitations")).json() == []
    await setup_user(late, p)
    assert len((await late.get("/v1/households/invitations")).json()) == 1
    assert (await late.post(f"/v1/households/{h['id']}/invitations/accept")).status_code == 200
    # someone else cannot accept it
    other = new_user()
    await setup_user(other)
    assert (await other.post(f"/v1/households/{h['id']}/invitations/accept")).status_code == 404


async def test_decline_and_reinvite(new_user):
    a, b = new_user(), new_user()
    await setup_user(a)
    pb = mobile()
    await setup_user(b, pb)
    h = (await a.post("/v1/households", {"name": "Home"})).json()
    body = {"display_name": "Bea", "role": "adult", "phone": pb}
    await a.post(f"/v1/households/{h['id']}/invitations", body)
    assert (await b.post(f"/v1/households/{h['id']}/invitations/decline")).status_code == 204
    assert (await b.get("/v1/households/invitations")).json() == []
    again = await a.post(f"/v1/households/{h['id']}/invitations", body)
    assert again.status_code == 201 and again.json()["status"] == "invited"
    assert len((await b.get("/v1/households/invitations")).json()) == 1


async def test_sharing_levels_drive_what_the_overview_shows(new_user):
    a, b, c, hid, _ = await family(new_user)
    acc_a, acc_b = await mk_account(a, balance_minor=1_000_000), await mk_account(b, balance_minor=500_000)
    await spend(a, acc_a["id"], 2_000_000, direction="credit", merchant="Acme Payroll")
    await spend(a, acc_a["id"], 100_000, "Groceries", "Carrefour")
    await spend(b, acc_b["id"], 300_000, direction="credit", merchant="Employer")
    await spend(b, acc_b["id"], 50_000, "Groceries", "Spinneys")
    await spend(b, acc_b["id"], 20_000, "Dining & Delivery", "Talabat")

    def by_name(ov, name):
        return next(m for m in ov["members"] if m["display_name"] == name)

    ov = (await a.get(f"/v1/households/{hid}/overview")).json()
    me, bea = by_name(ov, "Alex"), by_name(ov, "Bea")
    assert (
        me["is_you"]
        and me["you_can_see"] == "full"
        and me["shares"] == "nothing"
        and me["data"]["spend_minor"] == 100_000
    )
    assert bea["shares"] == "nothing" and bea["you_can_see"] == "nothing" and bea["data"] is None
    assert ov["household"]["members_included"] == 1 and ov["household"]["members_not_shared"] == 2  # Bea, Cal
    assert ov["household"]["income_minor"] == 2_000_000  # Bea's numbers are not in there
    # Bea shares totals
    assert (
        await b.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "totals"})
    ).status_code == 200
    ov = (await a.get(f"/v1/households/{hid}/overview")).json()
    bea = by_name(ov, "Bea")
    assert (
        bea["you_can_see"] == "totals"
        and bea["data"]["income_minor"] == 300_000
        and bea["data"]["spend_minor"] == 70_000
    )
    assert bea["data"]["categories"] is None and bea["data"]["transactions_available"] is False
    assert ov["household"]["income_minor"] == 2_300_000 and ov["household"]["spend_minor"] == 170_000
    assert ov["household"]["members_included"] == 2 and ov["household"]["category_members_included"] == 1
    # transactions still locked
    assert (await a.get(f"/v1/households/{hid}/members/{bea['member_id']}/transactions")).status_code == 403
    # categories
    await b.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "categories"})
    ov = (await a.get(f"/v1/households/{hid}/overview")).json()
    cats = {c["slug"]: c["total_minor"] for c in ov["household"]["categories"]}
    assert (
        cats == {"groceries": 150_000, "dining": 20_000} and ov["household"]["category_members_included"] == 2
    )
    assert (await a.get(f"/v1/households/{hid}/members/{bea['member_id']}/transactions")).status_code == 403
    # full: transactions visible, no notes, and the view is audited
    await b.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "full"})
    txns = (await a.get(f"/v1/households/{hid}/members/{bea['member_id']}/transactions")).json()
    assert {t["merchant"] for t in txns} == {"Employer", "Spinneys", "Talabat"} and "notes" not in txns[0]
    # Bea withdraws: everything closes again immediately
    await b.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "nothing"})
    assert (await a.get(f"/v1/households/{hid}/members/{bea['member_id']}/transactions")).status_code == 403
    assert by_name((await a.get(f"/v1/households/{hid}/overview")).json(), "Bea")["data"] is None
    # nobody can set someone else's level; you can read your own transactions
    me_id = me["member_id"]
    assert (await a.get(f"/v1/households/{hid}/members/{me_id}/transactions")).status_code == 200


async def test_transaction_views_are_audited(new_user, sessionmaker_):
    a, b, _, hid, _ = await family(new_user)
    acc = await mk_account(b)
    await spend(b, acc["id"], 1_000, merchant="Cafe")
    await b.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "full"})
    bea = next(
        m for m in (await a.get(f"/v1/households/{hid}")).json()["members"] if m["display_name"] == "Bea"
    )
    assert (await a.get(f"/v1/households/{hid}/members/{bea['id']}/transactions")).status_code == 200
    async with sessionmaker_() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.user_id == a.user_id, AuditLog.action == "household.view_transactions"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].entity_id == bea["id"]


async def test_dependents_see_only_themselves_and_cannot_invite(new_user):
    a, b, c, hid, _ = await family(new_user)
    await a.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "full"})
    await b.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "full"})
    await c.patch(f"/v1/households/{hid}/members/me/sharing", {"share_level": "categories"})
    ov = (await c.get(f"/v1/households/{hid}/overview")).json()
    seen = {m["display_name"]: m for m in ov["members"]}
    assert seen["Cal"]["you_can_see"] == "full" and seen["Cal"]["data"] is not None
    assert (
        seen["Alex"]["you_can_see"] == "nothing"
        and seen["Alex"]["data"] is None
        and seen["Bea"]["data"] is None
    )
    assert ov["household"]["members_included"] == 1
    alex = next(
        m for m in (await c.get(f"/v1/households/{hid}")).json()["members"] if m["display_name"] == "Alex"
    )
    assert (await c.get(f"/v1/households/{hid}/members/{alex['id']}/transactions")).status_code == 403
    assert (
        await c.post(
            f"/v1/households/{hid}/invitations",
            {"display_name": "Z", "role": "adult", "email": "z@example.com"},
        )
    ).status_code == 403
    # adults and the owner see Cal exactly as far as Cal chose
    cal = next(
        m
        for m in (await a.get(f"/v1/households/{hid}/overview")).json()["members"]
        if m["display_name"] == "Cal"
    )
    assert cal["you_can_see"] == "categories" and cal["data"]["categories"] is not None
    # an adult may invite
    inv = await b.post(
        f"/v1/households/{hid}/invitations",
        {"display_name": "Gran", "role": "adult", "email": "gran@example.com"},
    )
    assert inv.status_code == 201


async def test_roles_removal_leaving_and_isolation(new_user):
    a, b, c, hid, _ = await family(new_user)
    outsider = new_user()
    await setup_user(outsider)
    for path in ("", "/overview"):
        assert (await outsider.get(f"/v1/households/{hid}{path}")).status_code == 404
    members = {m["display_name"]: m["id"] for m in (await a.get(f"/v1/households/{hid}")).json()["members"]}
    # only the owner renames / changes roles / removes others
    assert (await b.patch(f"/v1/households/{hid}", {"name": "Mine"})).status_code == 403
    assert (await a.patch(f"/v1/households/{hid}", {"name": "Our Home"})).json()["name"] == "Our Home"
    assert (
        await b.patch(f"/v1/households/{hid}/members/{members['Cal']}", {"role": "adult"})
    ).status_code == 403
    assert (await a.patch(f"/v1/households/{hid}/members/{members['Cal']}", {"role": "adult"})).json()[
        "role"
    ] == "adult"
    assert (
        await a.patch(f"/v1/households/{hid}/members/{members['Alex']}", {"role": "adult"})
    ).status_code == 422
    assert (await b.delete(f"/v1/households/{hid}/members/{members['Cal']}")).status_code == 403
    assert (
        await a.delete(f"/v1/households/{hid}/members/{members['Alex']}")
    ).status_code == 422  # owner cannot leave
    # a member can leave; the owner can remove; removed people lose access
    assert (await b.delete(f"/v1/households/{hid}/members/{members['Bea']}")).status_code == 204
    assert (await b.get(f"/v1/households/{hid}")).status_code == 404
    assert (await a.delete(f"/v1/households/{hid}/members/{members['Cal']}")).status_code == 204
    assert (await c.get(f"/v1/households/{hid}/overview")).status_code == 404
    assert [m["display_name"] for m in (await a.get(f"/v1/households/{hid}")).json()["members"]] == ["Alex"]
    assert (await a.get(f"/v1/households/{hid}/overview?month=2026-13")).status_code == 422
    assert (await a.get(f"/v1/households/{hid}/overview?month=2026-03")).json()["month"] == "2026-03-01"
    assert (await b.delete(f"/v1/households/{hid}")).status_code == 404
    assert (await a.delete(f"/v1/households/{hid}")).status_code == 204
    assert (await a.get(f"/v1/households/{hid}")).status_code == 404 and (
        await a.get("/v1/households")
    ).json() == []
