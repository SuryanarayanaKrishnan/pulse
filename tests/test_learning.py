import pytest

from app.api.v1.routers import learning


@pytest.fixture(autouse=True)
def _reset():
    learning._ip_hits.clear()


def _batch(*pairs, fmt=1):
    return {"format": fmt, "map_version": 1, "pairs": [{"k": k, "c": c, "n": n} for k, c, n in pairs]}


async def test_accepts_clean_batch(make_client):
    r = await make_client().post(
        "/v1/category-contributions",
        json=_batch(("spinneys", "groceries", 2), ("noon minutes", "groceries", 1)),
    )
    assert r.status_code == 204


@pytest.mark.parametrize(
    "pair",
    [
        ("spinneys 1234", "groceries", 1),  # digits
        ("john@mail", "dining", 1),  # contact detail
        ("salary transfer", "dining", 1),  # transfer word
        ("spinneys", "salary", 1),  # people/account category
        ("spinneys", "family", 1),
        ("x", "dining", 1),  # too short
    ],
)
async def test_rejects_whole_batch_on_one_bad_pair(make_client, pair):
    r = await make_client().post(
        "/v1/category-contributions", json=_batch(("spinneys", "groceries", 1), pair)
    )
    assert r.status_code == 422


async def test_rejects_unknown_format_and_empty_and_oversize(make_client):
    c = make_client()
    assert (
        await c.post("/v1/category-contributions", json=_batch(("spinneys", "groceries", 1), fmt=2))
    ).status_code == 422
    assert (await c.post("/v1/category-contributions", json=_batch())).status_code == 422
    big = _batch(*[("spinneys", "groceries", 1)] * 201)
    assert (await c.post("/v1/category-contributions", json=big)).status_code == 422


async def test_rate_limited_per_ip(make_client):
    c = make_client()
    codes = [
        (await c.post("/v1/category-contributions", json=_batch(("spinneys", "groceries", 1)))).status_code
        for _ in range(21)
    ]
    assert codes == [204] * 20 + [429]


async def test_serves_the_shared_map(make_client):
    r = await make_client().get("/v1/merchant-map")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] >= 1 and len(body["entries"]) > 100
    assert set(body["entries"].values()) - learning.ALLOWED_CATEGORIES <= {
        "cc_payment",
        "family",
        "rent",
        "savings",
    }
