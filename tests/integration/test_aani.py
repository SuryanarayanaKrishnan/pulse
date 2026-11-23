"""Contacts matching, phone-identified split members and the Aani settlement state machine."""

import random
from uuid import uuid4

from app.services.phone import phone_hash


def mobile() -> str:
    """A fresh UAE mobile (local form); the test database is shared between runs."""
    return "05" + random.choice("024568") + "".join(random.choices("0123456789", k=7))


def e164(local: str) -> str:
    return "+971" + local[1:]


async def make_group(a, b=None, members=None):
    body = {"name": "Trip", "members": members or []}
    if b is not None:
        await b.get("/v1/me")  # provision
        body["members"].append({"display_name": "Bea", "user_id": str(b.user_id)})
    r = await a.post("/v1/splits/groups", body)
    assert r.status_code == 201, r.text
    return r.json()


def member(g, **kw):
    return next(m for m in g["members"] if all(m.get(k) == v for k, v in kw.items()))


async def test_profile_phone_normalised_hashed_and_unique(new_user):
    P1 = mobile()
    P2 = mobile()
    a, b = new_user(), new_user()
    r = await a.patch("/v1/me", {"phone": f"{P1[:3]} {P1[3:6]} {P1[6:]}", "aani_phone": P2})
    assert r.status_code == 200 and r.json()["phone"] == e164(P1)
    assert r.json()["aani_phone"] == e164(P2) and r.json()["phone_discoverable"] is True
    assert "phone_hash" not in r.json()
    bad = await a.patch("/v1/me", {"phone": "123"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_phone"
    landline = await a.patch("/v1/me", {"aani_phone": "04 123 4567"})
    assert landline.json()["error"]["code"] == "invalid_aani_phone"
    taken = await b.patch("/v1/me", {"phone": e164(P1)})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "phone_in_use"
    assert (await a.patch("/v1/me", {"display_name": "A"})).json()["phone"] == e164(P1)  # untouched
    assert (await a.patch("/v1/me", {"aani_phone": ""})).json()["aani_phone"] is None
    assert (await a.patch("/v1/me", {"phone": None})).json()["phone"] is None


async def test_contacts_match_only_hashes(new_user):
    P1 = mobile()
    P3 = mobile()
    P4 = mobile()
    a, b, c = new_user(), new_user(), new_user()
    await b.patch("/v1/me", {"phone": P1, "display_name": "Bea"})
    await c.patch("/v1/me", {"phone": P3, "phone_discoverable": False})
    await a.patch("/v1/me", {"phone": P4})
    hashes = [phone_hash(n) for n in (e164(P1), e164(P3), e164(P4), "+971500000000")]
    r = await a.post("/v1/contacts/match", {"hashes": hashes})
    assert r.status_code == 200
    assert r.json()["matches"] == [
        {"phone_hash": hashes[0], "user_id": str(b.user_id), "display_name": "Bea"}
    ]  # not the hidden one, not yourself, not unknown
    assert (await a.post("/v1/contacts/match", {"hashes": [e164(P1)]})).status_code == 422  # raw number
    assert (await a.post("/v1/contacts/match", {"hashes": []})).status_code == 422
    assert (await a.post("/v1/contacts/match", {"hashes": ["a" * 64] * 1001})).status_code == 422
    upper = await a.post("/v1/contacts/match", {"hashes": [hashes[0].upper()]})
    assert len(upper.json()["matches"]) == 1


async def test_members_by_phone_link_registered_users(new_user):
    P1 = mobile()
    P7 = mobile()
    a, b = new_user(), new_user()
    await b.patch("/v1/me", {"phone": P1})
    g = await make_group(a, members=[{"display_name": "Friend", "phone": f"{P1[:3]} {P1[3:6]} {P1[6:]}"}])
    friend = member(g, display_name="Friend")
    assert friend["user_id"] == str(b.user_id) and friend["phone"] == e164(P1)
    assert (await b.get(f"/v1/splits/groups/{g['id']}")).status_code == 200  # b now sees the group
    off = await make_group(a, members=[{"display_name": "Offline", "phone": P7}])
    assert member(off, display_name="Offline")["user_id"] is None
    dup = await a.post(f"/v1/splits/groups/{g['id']}/members", {"display_name": "X", "phone": e164(P1)})
    assert dup.status_code == 422 and dup.json()["error"]["code"] == "duplicate_member"
    bad = await a.post(f"/v1/splits/groups/{g['id']}/members", {"display_name": "X", "phone": "nope"})
    assert bad.json()["error"]["code"] == "invalid_phone"


async def test_aani_state_machine(new_user):
    P1 = mobile()
    P5 = mobile()
    P6 = mobile()
    payer, payee, stranger = new_user(), new_user(), new_user()
    await payee.patch("/v1/me", {"phone": P1, "aani_phone": P5, "display_name": "Pat"})
    g = await make_group(payer, payee)
    me, pat = member(g, user_id=str(payer.user_id)), member(g, user_id=str(payee.user_id))
    base = f"/v1/splits/groups/{g['id']}"
    # Payer paid 100 for a shared 200 expense: payee owes payer.
    await payer.post(
        f"{base}/expenses",
        {
            "description": "Hotel",
            "amount_minor": 20_000,
            "paid_by_member_id": me["id"],
            "participants": [{"member_id": me["id"]}, {"member_id": pat["id"]}],
        },
    )
    # here the *payee-of-the-debt* (Pat) pays Me, so Pat is the payer of the settlement
    req = await payee.post(
        f"{base}/settlements/aani-request", {"to_member_id": me["id"], "amount_minor": 10_000}
    )
    assert req.status_code == 422 and req.json()["error"]["code"] == "payee_phone_missing"
    await payer.patch("/v1/me", {"aani_phone": P6})
    req = await payee.post(
        f"{base}/settlements/aani-request",
        {"to_member_id": me["id"], "amount_minor": 10_000, "note": "hotel"},
    )
    assert req.status_code == 201, req.text
    body = req.json()
    assert body["payee_phone"] == e164(P6) and body["amount_display"] == "AED 100.00"
    assert body["payee_display_name"] == me["display_name"] and "Trip" in body["share_text"]
    assert e164(P6) in body["share_text"] and len(body["instructions"]) == 3
    st = body["settlement"]
    assert st["status"] == "pending" and st["method"] == "aani" and st["marked_paid_at"] is None
    assert st["can_mark_paid"] and not st["can_confirm"] and st["can_cancel"]
    sid = st["id"]

    def bal(r):
        return {b["member_id"]: b["net_minor"] for b in r.json()["balances"]}

    assert bal(await payer.get(f"{base}/balances"))[me["id"]] == 10_000  # pending does not count

    # wrong actors
    assert (await payer.post(f"{base}/settlements/{sid}/mark-paid")).status_code == 403
    assert (await payee.post(f"{base}/settlements/{sid}/confirm")).status_code == 403
    assert (await stranger.post(f"{base}/settlements/{sid}/confirm")).status_code == 404
    assert [s["id"] for s in (await payer.get("/v1/splits/settlements/awaiting-confirmation")).json()] == [
        sid
    ]
    assert (await payee.get("/v1/splits/settlements/awaiting-confirmation")).json() == []

    # payer marks paid (idempotent), still pending
    m1 = (await payee.post(f"{base}/settlements/{sid}/mark-paid")).json()
    m2 = (await payee.post(f"{base}/settlements/{sid}/mark-paid")).json()
    assert m1["status"] == "pending" and m1["marked_paid_at"] == m2["marked_paid_at"] is not None
    assert m1["can_mark_paid"] is False
    assert bal(await payer.get(f"{base}/balances"))[me["id"]] == 10_000
    # payee confirms -> counts
    c = await payer.post(f"{base}/settlements/{sid}/confirm")
    assert c.status_code == 200 and c.json()["status"] == "confirmed" and c.json()["confirmed_at"]
    assert bal(await payer.get(f"{base}/balances"))[me["id"]] == 0
    # terminal
    assert (await payer.post(f"{base}/settlements/{sid}/confirm")).json()["error"][
        "code"
    ] == "settlement_not_pending"
    assert (await payee.post(f"{base}/settlements/{sid}/mark-paid")).status_code == 409
    assert (await payee.delete(f"{base}/settlements/{sid}")).status_code == 409
    listed = (await payer.get(f"{base}/settlements?status=confirmed")).json()
    assert [s["id"] for s in listed] == [sid]
    assert (await payer.get(f"{base}/settlements?status=pending")).json() == []


async def test_cancel_pending_and_immediate_methods(new_user):
    P1 = mobile()
    a, b = new_user(), new_user()
    g = await make_group(a, b)
    me, bea = member(g, user_id=str(a.user_id)), member(g, user_id=str(b.user_id))
    base = f"/v1/splits/groups/{g['id']}"
    await b.patch("/v1/me", {"phone": P1})
    req = (
        await a.post(f"{base}/settlements/aani-request", {"to_member_id": bea["id"], "amount_minor": 500})
    ).json()
    assert (await a.delete(f"{base}/settlements/{req['settlement']['id']}")).status_code == 204
    assert (await a.get(f"{base}/settlements")).json() == []
    assert (await a.post(f"{base}/settlements/{req['settlement']['id']}/mark-paid")).status_code == 404
    # cash / default are confirmed immediately (previous behaviour)
    cash = (
        await a.post(
            f"{base}/settlements",
            {"from_member_id": me["id"], "to_member_id": bea["id"], "amount_minor": 700, "method": "cash"},
        )
    ).json()
    assert cash["status"] == "confirmed" and cash["method"] == "cash" and cash["confirmed_at"]
    legacy = (
        await a.post(
            f"{base}/settlements",
            {"from_member_id": me["id"], "to_member_id": bea["id"], "amount_minor": 100},
        )
    ).json()
    assert legacy["method"] == "other" and legacy["status"] == "confirmed"
    # generic POST with aani starts pending
    pend = (
        await a.post(
            f"{base}/settlements",
            {"from_member_id": me["id"], "to_member_id": bea["id"], "amount_minor": 100, "method": "aani"},
        )
    ).json()
    assert pend["status"] == "pending" and pend["can_cancel"]
    assert (await b.delete(f"{base}/settlements/{pend['id']}")).status_code == 204  # payee may reject
    assert (
        await a.post(
            f"{base}/settlements",
            {"from_member_id": me["id"], "to_member_id": bea["id"], "amount_minor": 1, "method": "paypal"},
        )
    ).status_code == 422


async def test_aani_for_unregistered_payee_uses_member_phone_and_creator_acts(new_user):
    P7 = mobile()
    a = new_user()
    g = await make_group(a, members=[{"display_name": "Offline", "phone": P7}])
    off = member(g, display_name="Offline")
    base = f"/v1/splits/groups/{g['id']}"
    req = await a.post(
        f"{base}/settlements/aani-request", {"to_member_id": off["id"], "amount_minor": 12_345}
    )
    assert req.json()["payee_phone"] == e164(P7) and req.json()["amount_display"] == "AED 123.45"
    sid = req.json()["settlement"]["id"]
    # the payee has no account, so the creator can confirm on their behalf
    assert req.json()["settlement"]["can_confirm"] is True
    assert (await a.post(f"{base}/settlements/{sid}/confirm")).json()["status"] == "confirmed"
    # cannot request a payment on behalf of someone else who has an account
    b = new_user()
    g2 = await make_group(a, b)
    bea = member(g2, user_id=str(b.user_id))
    other = member(g2, user_id=str(a.user_id))
    r = await a.post(
        f"/v1/splits/groups/{g2['id']}/settlements/aani-request",
        {"from_member_id": bea["id"], "to_member_id": other["id"], "amount_minor": 5},
    )
    assert r.status_code == 403
    r = await a.post(
        f"/v1/splits/groups/{g2['id']}/settlements/aani-request",
        {"to_member_id": str(uuid4()), "amount_minor": 5},
    )
    assert r.status_code == 422


async def test_aani_requires_aed(new_user):
    P8 = mobile()
    a, b = new_user(), new_user()
    await b.get("/v1/me")
    g = (
        await a.post(
            "/v1/splits/groups",
            {
                "name": "USD",
                "currency": "USD",
                "members": [{"display_name": "B", "user_id": str(b.user_id), "phone": P8}],
            },
        )
    ).json()
    _me, bea = member(g, user_id=str(a.user_id)), member(g, user_id=str(b.user_id))
    r = await a.post(
        f"/v1/splits/groups/{g['id']}/settlements/aani-request",
        {"to_member_id": bea["id"], "amount_minor": 5},
    )
    assert r.json()["error"]["code"] == "aani_requires_aed"
