"""Gmail OAuth + statement sync against real Postgres, with Google faked at the HTTP layer."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.integration import EmailConnection
from app.models.statement import Statement
from app.services.gmail.sync import sync_connection
from app.services.statements.processing import process_statement
from app.workers import tasks
from tests.integration.test_flows import mk_account
from tests.statement_fixtures import STYLES, build_card, render_pdf

pytestmark = pytest.mark.usefixtures("migrated_db")

ENBD_PDF = render_pdf(build_card(STYLES["enbd"]).lines)
ADCB_PDF = render_pdf(build_card(STYLES["adcb"]).lines, password="adcb-pw")


@pytest.fixture
def gsettings():
    return get_settings().model_copy(
        update={
            "google_client_id": "cid",
            "google_client_secret": "csecret",
            "google_redirect_uri": "https://api.example.com/cb",
            "oauth_state_secret": "t" * 40,
        }
    )


async def connect(api, google, code="good-code"):
    start = await api.post("/v1/email-connections/gmail/start")
    assert start.status_code == 200, start.text
    url = urlparse(start.json()["authorization_url"])
    state = parse_qs(url.query)["state"][0]
    return await api.raw_get(f"/v1/email-connections/gmail/callback?code={code}&state={state}"), state


async def get_conn(sessionmaker_, user_id) -> EmailConnection:
    async with sessionmaker_() as s:
        return (
            await s.execute(select(EmailConnection).where(EmailConnection.user_id == user_id))
        ).scalar_one()


class Collector:
    def __init__(self):
        self.ids: list[UUID] = []

    async def __call__(self, sid: UUID) -> None:
        self.ids.append(sid)


async def run_sync(sessionmaker_, conn_id, gsettings, encryptor, storage, google, enqueue, now=None):
    async with sessionmaker_() as s:
        conn = await s.get(EmailConnection, conn_id)
        async with google.client() as http:
            return await sync_connection(
                s,
                conn,
                settings=gsettings,
                encryptor=encryptor,
                storage=storage,
                http=http,
                enqueue=enqueue,
                now=now,
            )


async def test_oauth_flow_stores_encrypted_refresh_token(new_user, queue, google, sessionmaker_, encryptor):
    api = new_user()
    assert (await api.post("/v1/email-connections/gmail/start")).status_code == 200
    resp, _ = await connect(api, google)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected" and body["email"] == "me@example.com"

    conn = await get_conn(sessionmaker_, api.user_id)
    assert conn.provider == "gmail" and conn.email == "me@example.com" and conn.status == "active"
    assert (
        conn.encrypted_refresh_token != "rt-1" and encryptor.decrypt(conn.encrypted_refresh_token) == "rt-1"
    )
    assert "gmail.readonly" in (conn.scopes or "")
    assert [j[0] for j in queue.jobs] == ["sync_gmail_connection"]

    listing = (await api.get("/v1/email-connections")).json()
    assert len(listing) == 1 and listing[0]["email"] == "me@example.com"
    assert "rt-1" not in str(listing) and "token" not in " ".join(listing[0])

    # connecting the same mailbox again updates the row instead of adding one
    google.refresh_token = "rt-2"
    again, _ = await connect(api, google, code="another-code")
    assert again.status_code == 200
    assert len((await api.get("/v1/email-connections")).json()) == 1
    assert encryptor.decrypt((await get_conn(sessionmaker_, api.user_id)).encrypted_refresh_token) == "rt-2"


async def test_oauth_callback_rejects_bad_requests(new_user, google):
    api = new_user()
    start = await api.post("/v1/email-connections/gmail/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    assert (await api.raw_get("/v1/email-connections/gmail/callback?code=x&state=garbage")).status_code == 400
    assert (await api.raw_get("/v1/email-connections/gmail/callback")).status_code == 400
    denied = await api.raw_get("/v1/email-connections/gmail/callback?error=access_denied&state=" + state)
    assert denied.status_code == 400 and denied.json()["status"] == "denied"
    bad_code = await api.raw_get(f"/v1/email-connections/gmail/callback?code=bad-code&state={state}")
    assert bad_code.status_code == 400 and bad_code.json()["error"]["code"] == "oauth_error"

    google.refresh_token = None  # Google omitted the refresh token
    no_refresh, _ = await connect(api, google, code="fresh-1")
    assert no_refresh.status_code == 400 and "refresh token" in no_refresh.json()["error"]["message"]
    google.refresh_token = "rt-1"
    google.scope = "openid"  # user unticked the Gmail permission
    no_scope, _ = await connect(api, google, code="fresh-2")
    assert no_scope.status_code == 400 and "gmail.readonly" in no_scope.json()["error"]["message"]
    assert (await api.get("/v1/email-connections")).json() == []


async def test_callback_shows_a_plain_page_in_a_browser(new_user, google):
    api = new_user()
    start = await api.post("/v1/email-connections/gmail/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    html = {"accept": "text/html,application/xhtml+xml"}
    denied = await api.raw_get(
        "/v1/email-connections/gmail/callback?error=access_denied&state=" + state, headers=html
    )
    assert denied.status_code == 400 and "text/html" in denied.headers["content-type"]
    assert "not connected" in denied.text
    ok = await api.raw_get(f"/v1/email-connections/gmail/callback?code=good-code&state={state}", headers=html)
    assert ok.status_code == 200 and "Gmail connected" in ok.text and "me@example.com" not in ok.text


async def test_start_requires_auth_and_config(new_user, make_client):
    api = new_user()
    assert (await api.c.post("/v1/email-connections/gmail/start")).status_code == 401


async def test_sync_imports_statement_pdfs_idempotently(
    new_user, queue, google, sessionmaker_, encryptor, storage, gsettings
):
    api = new_user()
    await mk_account(api, type="credit_card", institution="Emirates NBD", last4="1234")
    await connect(api, google)
    conn = await get_conn(sessionmaker_, api.user_id)

    google.add_email("m-enbd", "Emirates NBD <statements@emiratesnbd.com>", ENBD_PDF)
    google.add_email("m-adcb", "ADCB <estatement@adcb.com>", ADCB_PDF, filename="ADCB eStatement.pdf")
    google.add_email("m-spoof", "Emirates NBD <statements@evil.example>", ENBD_PDF + b"\n%spoof")
    google.leaky = {"m-spoof"}  # Gmail returned it for some query, e.g. via a lookalike display name
    google.add_email("m-nopdf", "Emirates NBD <statements@emiratesnbd.com>", None)
    google.add_email(
        "m-notpdf", "Mashreq <noreply@mashreq.com>", b"MZ not really a pdf", filename="Statement.pdf"
    )

    enqueued = Collector()
    r1 = await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, enqueued)
    assert (r1.status, r1.statements_created, r1.duplicates) == ("ok", 2, 0)
    assert r1.skipped >= 3  # spoofed sender (seen once per institution query), no pdf, not a pdf
    assert len(enqueued.ids) == 2
    # one search per institution, each limited to PDF attachments and a lower date bound
    assert len(google.queries) == 12 and all(
        "has:attachment filename:pdf" in q and "after:" in q for q in google.queries
    )

    async with sessionmaker_() as s:
        rows = {
            r.external_id: r
            for r in (await s.execute(select(Statement).where(Statement.user_id == api.user_id))).scalars()
        }
    assert set(rows) == {"gmail:m-enbd:0", "gmail:m-adcb:0"}
    enbd = rows["gmail:m-enbd:0"]
    assert (enbd.source, enbd.institution, enbd.email_connection_id, enbd.parse_status.value) == (
        "gmail",
        "enbd",
        conn.id,
        "pending",
    )
    assert (
        enbd.original_filename == "Statement.pdf"
        and enbd.file_storage_key
        and await storage.get(enbd.file_storage_key) == ENBD_PDF
    )

    # second run: nothing new, and known attachments are not even downloaded again
    fetches = google.attachment_fetches
    r2 = await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, enqueued)
    assert (r2.statements_created, r2.duplicates) == (0, 2) and len(enqueued.ids) == 2
    assert (
        google.attachment_fetches == fetches + 1
    )  # only the not-yet-known non-statement attachment is re-checked

    # the same PDF forwarded in a new message is a content duplicate, a new statement is created once
    google.add_email("m-new", "Emirates NBD <statements@emiratesnbd.com>", ENBD_PDF)
    r3 = await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, enqueued)
    assert (r3.statements_created, r3.duplicates) == (0, 3)
    google.add_email(
        "m-feb",
        "Emirates NBD <statements@emiratesnbd.com>",
        render_pdf(build_card(STYLES["enbd"], opening=5).lines),
    )
    r4 = await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, enqueued)
    assert r4.statements_created == 1

    # the pipeline picks them up: ENBD parses, ADCB (password protected) waits for a password
    for sid in enqueued.ids:
        async with sessionmaker_() as s:
            await process_statement(s, sid, storage, encryptor)
    listing = (await api.get("/v1/statements?limit=50")).json()
    by_status = sorted((s["institution"], s["parse_status"]) for s in listing)
    assert by_status == [("adcb", "needs_password"), ("enbd", "parsed"), ("enbd", "parsed")]
    assert all(s["source"] == "gmail" for s in listing)
    assert (await get_conn(sessionmaker_, api.user_id)).last_synced_at is not None


async def test_incremental_window_uses_last_sync_with_overlap(
    new_user, google, sessionmaker_, encryptor, storage, gsettings
):
    api = new_user()
    await connect(api, google)
    conn = await get_conn(sessionmaker_, api.user_id)
    now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, Collector(), now=now)
    assert (
        f"after:{(now - timedelta(days=gsettings.gmail_initial_lookback_days)):%Y/%m/%d}" in google.queries[0]
    )
    google.queries.clear()
    later = now + timedelta(days=10)
    await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, Collector(), now=later)
    assert f"after:{(now - timedelta(days=gsettings.gmail_sync_overlap_days)):%Y/%m/%d}" in google.queries[0]


async def test_revoked_token_marks_connection_needs_reauth_then_reconnect_heals(
    new_user, google, sessionmaker_, encryptor, storage, gsettings
):
    api = new_user()
    await connect(api, google)
    conn = await get_conn(sessionmaker_, api.user_id)
    google.invalid_grant = True
    res = await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, Collector())
    assert res.status == "needs_reauth"
    listed = (await api.get("/v1/email-connections")).json()[0]
    assert listed["status"] == "needs_reauth" and "rejected" in listed["last_error"]

    google.invalid_grant = False
    google.refresh_token = "rt-new"
    healed, _ = await connect(api, google, code="code-2")
    assert healed.status_code == 200
    assert (await api.get("/v1/email-connections")).json()[0]["status"] == "active"


async def test_partial_failure_keeps_window_open(
    new_user, google, sessionmaker_, encryptor, storage, gsettings
):
    api = new_user()
    await connect(api, google)
    conn = await get_conn(sessionmaker_, api.user_id)
    google.add_email("m-ok", "Emirates NBD <statements@emiratesnbd.com>", ENBD_PDF)
    google.messages["m-broken"] = {
        "id": "m-broken",
        "payload": {
            "headers": [{"name": "From", "value": "x <a@adcb.com>"}],
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "a.pdf",
                    "body": {"attachmentId": "missing", "size": 5},
                }
            ],
        },
    }
    res = await run_sync(sessionmaker_, conn.id, gsettings, encryptor, storage, google, Collector())
    assert res.status == "partial" and res.statements_created == 1 and res.errors
    after = await get_conn(sessionmaker_, api.user_id)
    assert after.last_synced_at is None and after.last_error


async def test_disconnect_revokes_and_stops_sync(
    new_user, queue, google, sessionmaker_, encryptor, storage, monkeypatch, gsettings
):
    api, other = new_user(), new_user()
    await connect(api, google)
    conn = await get_conn(sessionmaker_, api.user_id)
    assert (await other.delete(f"/v1/email-connections/{conn.id}")).status_code == 404
    assert (await other.post(f"/v1/email-connections/{conn.id}/sync")).status_code == 404
    assert (await api.post(f"/v1/email-connections/{conn.id}/sync")).status_code == 202
    assert queue.jobs[-1][0] == "sync_gmail_connection"
    assert (await api.delete(f"/v1/email-connections/{conn.id}")).status_code == 204
    assert google.revoked == ["rt-1"]
    assert (await api.get("/v1/email-connections")).json() == []

    monkeypatch.setattr(tasks, "get_encryptor", lambda: encryptor)
    monkeypatch.setattr(tasks, "get_settings", lambda: gsettings)
    ctx = {"session_factory": sessionmaker_, "storage": storage, "http": google.client(), "redis": queue}
    assert (await tasks.sync_gmail_connection(ctx, str(conn.id))) == {"status": "skipped"}


async def test_worker_jobs_fan_out_and_sync(
    new_user, queue, google, sessionmaker_, encryptor, storage, monkeypatch, gsettings
):
    a, b = new_user(), new_user()
    await connect(a, google)
    google.refresh_token = "rt-b"
    google.email = "b@example.com"
    await connect(b, google, code="code-b")
    conn_b = await get_conn(sessionmaker_, b.user_id)
    async with sessionmaker_() as s:
        row = await s.get(EmailConnection, conn_b.id)
        row.status = "needs_reauth"  # skipped by the cron fan-out
        await s.commit()
    queue.jobs.clear()
    ctx = {"session_factory": sessionmaker_, "storage": storage, "http": google.client(), "redis": queue}
    await tasks.sync_all_gmail_connections(ctx)  # the shared test DB may hold other tests' connections
    queued = {j[1][0] for j in queue.jobs if j[0] == "sync_gmail_connection"}
    assert str((await get_conn(sessionmaker_, a.user_id)).id) in queued and str(conn_b.id) not in queued

    google.refresh_token = "rt-1"
    google.add_email("m1", "Emirates NBD <statements@emiratesnbd.com>", ENBD_PDF)
    monkeypatch.setattr(tasks, "get_encryptor", lambda: encryptor)
    monkeypatch.setattr(tasks, "get_settings", lambda: gsettings)
    queue.jobs.clear()
    out = await tasks.sync_gmail_connection(ctx, str((await get_conn(sessionmaker_, a.user_id)).id))
    assert out == {"status": "ok", "created": 1, "duplicates": 0}
    assert [j[0] for j in queue.jobs] == ["process_statement"]
