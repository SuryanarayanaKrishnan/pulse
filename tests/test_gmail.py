import base64
import time
from datetime import date
from uuid import uuid4

import httpx
import jwt
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.services.gmail.client import GmailApiError, GmailClient, pdf_attachments, sender_address
from app.services.gmail.oauth import (
    GmailNotConfigured,
    InvalidGrant,
    InvalidState,
    OAuthError,
    build_authorization_url,
    exchange_code,
    make_state,
    read_state,
    refresh_access_token,
    revoke_token,
)
from app.services.gmail.sync import build_query
from app.services.institutions import institutions
from tests.fake_google import FakeGoogle, b64


def settings(**over) -> Settings:
    base = {
        "google_client_id": "cid.apps.googleusercontent.com",
        "google_client_secret": "csecret",
        "google_redirect_uri": "https://api.example.com/v1/email-connections/gmail/callback",
        "oauth_state_secret": "s" * 40,
    }
    return Settings(**{**base, **over})


def test_state_roundtrip_expiry_and_tamper():
    s, uid = settings(), uuid4()
    assert read_state(s, make_state(s, uid)) == uid
    with pytest.raises(InvalidState):
        read_state(s, make_state(s, uid, now=time.time() - 3600))
    with pytest.raises(InvalidState):
        read_state(s, make_state(s, uid) + "x")
    with pytest.raises(InvalidState):
        read_state(settings(oauth_state_secret="o" * 40), make_state(s, uid))
    other = jwt.encode(
        {"sub": str(uid), "purpose": "login", "exp": time.time() + 60}, s.state_secret, algorithm="HS256"
    )
    with pytest.raises(InvalidState):
        read_state(s, other)
    with pytest.raises(InvalidState):
        read_state(
            s,
            jwt.encode(
                {"sub": "not-a-uuid", "purpose": "gmail_oauth", "exp": time.time() + 60},
                s.state_secret,
                algorithm="HS256",
            ),
        )


def test_state_secret_falls_back_to_first_fernet_key():
    from app.core.crypto import generate_key

    k = generate_key()
    s = settings(oauth_state_secret="", fernet_keys=[k])
    assert s.state_secret == k and read_state(s, make_state(s, uuid4()))


def test_authorization_url_requests_offline_readonly_access():
    from urllib.parse import parse_qs, urlparse

    url = build_authorization_url(settings(), "STATE")
    p = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(p.query).items()}
    assert p.netloc == "accounts.google.com"
    assert q["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert (q["access_type"], q["prompt"], q["response_type"], q["state"]) == (
        "offline",
        "consent",
        "code",
        "STATE",
    )
    assert q["redirect_uri"].endswith("/v1/email-connections/gmail/callback") and q["client_id"].startswith(
        "cid"
    )


def test_not_configured_raises_503():
    with pytest.raises(GmailNotConfigured) as e:
        build_authorization_url(Settings(oauth_state_secret="x" * 40), "s")
    assert e.value.status_code == 503
    with pytest.raises(GmailNotConfigured):
        build_authorization_url(settings(oauth_state_secret=""), "s")


async def test_token_exchange_refresh_and_revoke():
    g = FakeGoogle()
    async with g.client() as http:
        t = await exchange_code(http, settings(), "good-code")
        assert (t.access_token, t.refresh_token) == ("at-1", "rt-1") and "gmail.readonly" in t.scope
        with pytest.raises(OAuthError):  # the code is single-use
            await exchange_code(http, settings(), "good-code")
        assert await refresh_access_token(http, settings(), "rt-1") == "at-2"
        with pytest.raises(InvalidGrant):
            await refresh_access_token(http, settings(), "revoked")
        assert await revoke_token(http, "rt-1") is True and g.revoked == ["rt-1"]


async def test_revoke_swallows_network_errors():
    def boom(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        assert await revoke_token(http, "x") is False


def test_build_query_per_institution():
    enbd = institutions.get("enbd")
    assert enbd is not None
    q = build_query(enbd, date(2026, 1, 5))
    assert (
        q
        == 'has:attachment filename:pdf (from:emiratesnbd.com) (subject:"statement" OR subject:"e-statement" OR subject:"estatement") after:2026/01/05'
    )
    hsbc = institutions.get("hsbc")
    assert hsbc is not None and "from:hsbc.ae OR from:hsbc.com" in build_query(hsbc, date(2026, 1, 1))
    assert all(i.email_domains for i in institutions)  # every institution is searchable


def test_sender_matching_uses_domain_not_display_name():
    assert institutions.from_email_address("statements@emiratesnbd.com") == "enbd"
    assert institutions.from_email_address("noreply@email.emiratesnbd.com") == "enbd"
    assert institutions.from_email_address("emiratesnbd.com@evil.com") is None
    assert institutions.from_email_address("a@notemiratesnbd.com") is None
    assert institutions.from_email_address(None) is None
    assert (
        sender_address(
            {"payload": {"headers": [{"name": "From", "value": "Emirates NBD <Statements@EmiratesNBD.com>"}]}}
        )
        == "statements@emiratesnbd.com"
    )


def test_pdf_attachment_walker():
    def att(mime, name, size=10, aid="a"):
        return {"mimeType": mime, "filename": name, "body": {"attachmentId": aid, "size": size}}

    msg = {
        "payload": {
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/html", "filename": "", "body": {"size": 1}}],
                },
                att("application/pdf", "statement.pdf", aid="1"),
                att("image/png", "logo.png", aid="2"),
                att("application/octet-stream", "Terms.PDF", aid="3"),
                {
                    "mimeType": "multipart/mixed",
                    "parts": [att("application/pdf", "big.pdf", size=10**9, aid="4")],
                },
                att("application/pdf", "second.pdf", aid="5"),
            ]
        }
    }
    got = list(pdf_attachments(msg, max_bytes=1000))
    assert [(a.filename, a.attachment_id) for a in got] == [
        ("statement.pdf", "1"),
        ("Terms.PDF", "3"),
        ("second.pdf", "5"),
    ]
    assert [a.index for a in got] == [
        0,
        1,
        3,
    ]  # index counts every PDF so ids stay stable if the size limit changes


async def test_client_pagination_retry_and_errors():
    g = FakeGoogle()
    for i in range(3):
        g.add_email(f"m{i}", "x@emiratesnbd.com", b"%PDF-1.4")
    async with g.client() as http:
        c = GmailClient(http, "at-1", backoff=0)
        g.flaky_search = 2
        assert sorted(await c.search("has:attachment from:emiratesnbd.com")) == ["m0", "m1", "m2"]
        assert len(await c.search("has:attachment from:emiratesnbd.com", limit=2)) == 2
        assert await c.profile_email() == "Me@Example.com"
        data = base64.urlsafe_b64decode(b64(b"%PDF-1.4 x") + "==")
        assert data.startswith(b"%PDF")
        assert (await c.get_attachment("m0", "att-m0")) == b"%PDF-1.4"
        g.flaky_search = 99
        with pytest.raises(GmailApiError) as e:
            await c.search("has:attachment from:emiratesnbd.com")
        assert e.value.status == 503
        bad = GmailClient(http, "wrong-token")
        with pytest.raises(GmailApiError) as e2:
            await bad.profile_email()
        assert e2.value.status == 401


def test_app_error_types():
    assert issubclass(OAuthError, AppError) and issubclass(InvalidState, AppError)
