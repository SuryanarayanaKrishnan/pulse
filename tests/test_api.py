from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import security
from app.models.user import User
from tests.conftest import FakeQueue, FakeSession


def error_of(resp):
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert {"code", "message", "details", "request_id"} <= set(err)
    return err


async def test_health_and_request_id(make_client):
    c = make_client()
    r = await c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert len(r.headers["x-request-id"]) >= 8
    r2 = await c.get("/health", headers={"X-Request-ID": "my-trace-id-123"})
    assert r2.headers["x-request-id"] == "my-trace-id-123"
    r3 = await c.get("/health", headers={"X-Request-ID": "bad id with spaces!"})
    assert r3.headers["x-request-id"] != "bad id with spaces!"


async def test_ready_ok_and_degraded(make_client):
    ok = await make_client(FakeSession(), FakeQueue()).get("/ready")
    assert ok.status_code == 200 and ok.json()["checks"] == {"database": "ok", "redis": "ok"}
    bad = await make_client(FakeSession(fail=True), FakeQueue(fail=True)).get("/ready")
    assert bad.status_code == 503
    assert bad.json()["checks"] == {"database": "fail", "redis": "fail"}


async def test_error_envelope_for_404_and_request_id_matches_header(make_client):
    r = await make_client().get("/nope")
    err = error_of(r)
    assert r.status_code == 404 and err["code"] == "not_found"
    assert err["request_id"] == r.headers["x-request-id"]


async def test_missing_and_malformed_token(make_client):
    c = make_client()
    r = await c.get("/v1/me")
    assert r.status_code == 401 and error_of(r)["code"] == "missing_token"
    assert r.headers["www-authenticate"] == "Bearer"
    r = await c.get("/v1/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401 and error_of(r)["code"] == "invalid_token"


async def test_rejects_expired_wrong_secret_wrong_audience(make_client, make_token):
    c = make_client()
    for tok in (
        make_token(exp_in=-10),
        make_token(secret="x" * 40),
        make_token(aud="anon"),
    ):
        r = await c.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


async def test_rejects_alg_none(make_client):
    tok = jwt.encode({"sub": str(uuid4()), "aud": "authenticated"}, key=None, algorithm="none")
    r = await make_client().get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


async def test_me_with_valid_hs256_token(make_client, make_token):
    uid = uuid4()
    user = User(id=uid, email="tester@example.com", display_name="T", default_currency="AED")
    c = make_client(FakeSession(user))
    r = await c.get("/v1/me", headers={"Authorization": f"Bearer {make_token(uid)}"})
    assert r.status_code == 200
    assert r.json() == {
        "id": str(uid),
        "email": "tester@example.com",
        "display_name": "T",
        "default_currency": "AED",
    }


async def test_deactivated_user_rejected(make_client, make_token):
    from datetime import UTC, datetime

    uid = uuid4()
    user = User(id=uid, email=None, default_currency="AED", deleted_at=datetime.now(UTC))
    r = await make_client(FakeSession(user)).get(
        "/v1/me", headers={"Authorization": f"Bearer {make_token(uid)}"}
    )
    assert r.status_code == 401 and error_of(r)["code"] == "account_deactivated"


async def test_jwks_rs256_path(make_client, make_token, monkeypatch, settings):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class StubClient:
        def get_signing_key_from_jwt(self, token: str):
            class K:
                key = private.public_key()

            return K()

    uid = uuid4()
    tok = make_token(uid, alg="RS256", key=private)
    headers = {"Authorization": f"Bearer {tok}"}
    user = User(id=uid, email=None, default_currency="AED")
    c = make_client(FakeSession(user))

    # Not configured => rejected.
    assert (await c.get("/v1/me", headers=headers)).status_code == 401

    monkeypatch.setattr(settings, "supabase_jwks_url", "https://example.invalid/jwks.json")
    monkeypatch.setattr(security, "_jwks_client", lambda url: StubClient())
    assert (await c.get("/v1/me", headers=headers)).status_code == 200

    # A token signed by a different key must fail against the JWKS key.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad = make_token(uid, alg="RS256", key=other)
    assert (await c.get("/v1/me", headers={"Authorization": f"Bearer {bad}"})).status_code == 401


async def test_validation_error_envelope(make_client, make_token):
    uid = uuid4()
    c = make_client(FakeSession(User(id=uid, default_currency="AED")))
    r = await c.post(
        "/v1/accounts",
        json={"type": "piggybank", "name": "", "currency": "aed"},
        headers={"Authorization": f"Bearer {make_token(uid)}"},
    )
    err = error_of(r)
    assert r.status_code == 422 and err["code"] == "validation_error"
    assert {d["loc"][-1] for d in err["details"]} >= {"type", "name", "currency"}


async def test_ingest_requires_content(make_client, make_token):
    uid = uuid4()
    c = make_client(FakeSession(User(id=uid, default_currency="AED")))
    r = await c.post(
        "/v1/ingest/sms", json={"sender": "ADCB"}, headers={"Authorization": f"Bearer {make_token(uid)}"}
    )
    assert r.status_code == 422
    r = await c.post("/v1/ingest/sms", json={"raw_text": "x"})
    assert r.status_code == 401


async def test_cors_preflight(make_client):
    r = await make_client().options(
        "/v1/me",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    bad = await make_client().options(
        "/v1/me", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in bad.headers


async def test_unhandled_exception_returns_envelope(make_client, make_token, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app = make_client()._transport.app  # type: ignore[attr-defined]

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    c = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")
    r = await c.get("/boom")
    assert r.status_code == 500 and error_of(r)["code"] == "internal_error"
    assert "kaboom" not in r.text
