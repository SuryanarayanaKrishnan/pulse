import httpx
import pytest

from app.api.v1.routers import bug_reports
from app.api.v1.routers.bug_reports import get_issue_client
from app.core.config import get_settings

BODY = {
    "title": "[Bug] Chart looks wrong",
    "category": "bug",
    "body": "# report",
    "device": {"platform": "ios"},
}


@pytest.fixture(autouse=True)
def _reset():
    bug_reports._ip_hits.clear()
    bug_reports._day_hits.clear()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _github(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.method == "PUT":
            return httpx.Response(201, json={})
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/7"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_not_configured_is_503_so_the_app_falls_back(make_client, monkeypatch):
    monkeypatch.delenv("BUG_REPORT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("BUG_REPORT_REPO", raising=False)
    r = await make_client().post("/v1/bug-reports", json=BODY)
    assert r.status_code == 503


async def test_files_issue_with_screenshot_and_labels(make_client, monkeypatch):
    monkeypatch.setenv("BUG_REPORT_GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("BUG_REPORT_REPO", "o/r")
    monkeypatch.setenv("BUG_REPORT_SCREENSHOTS", "true")
    calls: list[httpx.Request] = []
    c = make_client()
    c._transport.app.dependency_overrides[get_issue_client] = lambda: _github(calls)  # type: ignore[attr-defined]
    r = await c.post("/v1/bug-reports", json={**BODY, "screenshot_png_base64": "aGk="})
    assert r.status_code == 201 and r.json() == {"issue_url": "https://github.com/o/r/issues/7"}
    assert [x.method for x in calls] == ["PUT", "POST"]
    issue = calls[1].read().decode()
    assert "beta-report" in issue and "Screenshot" in issue
    assert "from-app" in issue and '"ios"' in issue
    assert "t0ken" not in r.text


async def test_rejects_bad_category_and_rate_limits(make_client, monkeypatch):
    monkeypatch.setenv("BUG_REPORT_GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("BUG_REPORT_REPO", "o/r")
    c = make_client()
    c._transport.app.dependency_overrides[get_issue_client] = lambda: _github([])  # type: ignore[attr-defined]
    assert (await c.post("/v1/bug-reports", json={**BODY, "category": "spam"})).status_code == 422
    codes = [(await c.post("/v1/bug-reports", json=BODY)).status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]


async def test_public_repo_default_withholds_screenshot_and_scrubs(make_client, monkeypatch):
    monkeypatch.setenv("BUG_REPORT_GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("BUG_REPORT_REPO", "o/r")
    calls: list[httpx.Request] = []
    c = make_client()
    c._transport.app.dependency_overrides[get_issue_client] = lambda: _github(calls)  # type: ignore[attr-defined]
    r = await c.post(
        "/v1/bug-reports",
        json={**BODY, "body": "mail a.b@x.com paid 1234567 AED", "screenshot_png_base64": "aGk="},
    )
    assert r.status_code == 201
    assert [x.method for x in calls] == ["POST"]
    issue = calls[0].read().decode()
    assert "a.b@x.com" not in issue and "1234567" not in issue and "Withheld" in issue
