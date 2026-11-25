import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app
from tests.conftest import FakeSession

SECRET = "test-secret-test-secret-test-secret-123456"


@pytest.fixture
def dev_on(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_AUTH", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("ALLOW_DEV_AUTH")
    get_settings.cache_clear()


async def test_route_absent_by_default(make_client):
    r = await make_client().post("/v1/dev/token")
    assert r.status_code == 404


async def test_token_minted_and_accepted_by_normal_auth(dev_on, make_client):
    from app.models.user import User

    c = make_client()
    r = await c.post("/v1/dev/token")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    # The token is a normal bearer JWT: the regular verifier accepts it.
    from app.core.security import verify_token

    claims = await verify_token(body["access_token"], get_settings())
    assert str(claims.sub) == body["user_id"]
    # And a protected route no longer 401s with it.
    from uuid import UUID

    user = User(id=UUID(body["user_id"]), email="dev@pulse.local", default_currency="AED")
    c2 = make_client(FakeSession(user=user))
    me = await c2.get("/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200


@pytest.mark.parametrize("env", ["production", "staging"])
def test_refuses_to_start_outside_dev(env):
    with pytest.raises(ValidationError):
        Settings(app_env=env, allow_dev_auth=True, supabase_jwt_secret=SECRET)


def test_requires_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="development", allow_dev_auth=True, supabase_jwt_secret="")


def test_create_app_fails_in_production(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_AUTH", "true")
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
