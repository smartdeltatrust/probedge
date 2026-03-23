"""
Tests de integración para el backend FastAPI.
Requiere API keys válidas en .env (Massive + FMP + Stripe).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from api.core.config import settings
from api.core.database import AsyncSessionLocal
from api.credits.service import CreditService
from api.main import app

TICKER = "SPY"
EXPIRY = "2026-03-23"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _ensure_test_user(email: str, password: str, client: AsyncClient):
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    if resp.status_code == 201:
        data = resp.json()
    else:
        login = await client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        data = login.json()
    user_id = data["user"]["id"]
    headers = {"Authorization": f"Bearer {data['tokens']['access_token']}"}
    refresh_token = data["tokens"]["refresh_token"]

    async with AsyncSessionLocal() as session:
        credits = CreditService(session)
        await credits.add_credits(user_id, 2000, "test top-up")

    return {"headers": headers, "refresh": refresh_token, "user_id": user_id}


@pytest.fixture(scope="module")
async def auth_user(client):
    email = f"test_{uuid4().hex}@example.com"
    password = "TestPass123!"
    return await _ensure_test_user(email, password, client)


@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.anyio
async def test_register_and_login_flow(client):
    email = f"user_{uuid4().hex}@example.com"
    password = "SecurePass123!"
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    data = resp.json()
    assert "tokens" in data
    assert data["user"]["email"] == email.lower()

    login = await client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    tokens = login.json()["tokens"]
    assert "access_token" in tokens


@pytest.mark.anyio
async def test_refresh_token(client, auth_user):
    resp = await client.post("/auth/refresh", json={"refresh_token": auth_user["refresh"]})
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"


@pytest.mark.anyio
async def test_protected_endpoint_requires_auth(client):
    r = await client.get(f"/options/{TICKER}/expiries")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_expiries_returns_list(client, auth_user):
    r = await client.get(f"/options/{TICKER}/expiries", headers=auth_user["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == TICKER
    assert isinstance(data["expiries"], list)


@pytest.mark.anyio
async def test_chain_returns_contracts(client, auth_user):
    r = await client.get(
        f"/options/{TICKER}/chain",
        params={"expiration": EXPIRY, "limit": 5},
        headers=auth_user["headers"],
    )
    assert r.status_code == 200
    data = r.json()
    assert data["returned"] <= 5


@pytest.mark.anyio
async def test_rnd_is_valid_density(client, auth_user):
    r = await client.get(f"/options/{TICKER}/rnd", params={"expiration": EXPIRY}, headers=auth_user["headers"])
    assert r.status_code == 200
    data = r.json()
    pg = [v for v in data["price_grid"] if v is not None]
    rnd = [v for v in data["rnd"] if v is not None]
    assert len(pg) == len(rnd)
    integral = np.trapezoid(rnd, pg)
    assert abs(integral - 1.0) < 0.05


@pytest.mark.anyio
async def test_market_quote(client, auth_user):
    r = await client.get(f"/market/{TICKER}/quote", headers=auth_user["headers"])
    assert r.status_code == 200
    assert r.json()["ticker"] == TICKER


@pytest.mark.anyio
async def test_market_history(client, auth_user):
    r = await client.get(f"/market/{TICKER}/history", params={"days": 3}, headers=auth_user["headers"])
    assert r.status_code == 200
    assert r.json()["days"] <= 3


@pytest.mark.anyio
async def test_credit_consumption(client, auth_user):
    before = await client.get("/credits/wallet", headers=auth_user["headers"])
    resp = await client.get(
        f"/options/{TICKER}/probabilities",
        params={"expiration": EXPIRY, "price_target": 400},
        headers=auth_user["headers"],
    )
    assert resp.status_code == 200
    after = await client.get("/credits/wallet", headers=auth_user["headers"])
    assert after.json()["balance"] < before.json()["balance"]


@pytest.mark.anyio
async def test_rate_limit_basic_plan(client, auth_user, monkeypatch):
    from api.core import rate_limit as rate_module

    monkeypatch.setitem(rate_module.PLAN_LIMITS, "basic", "2/minute")
    for _ in range(2):
        r = await client.get("/credits/wallet", headers=auth_user["headers"])
        assert r.status_code == 200
    r = await client.get("/credits/wallet", headers=auth_user["headers"])
    assert r.status_code == 429
    monkeypatch.setitem(rate_module.PLAN_LIMITS, "basic", "50/hour")


@pytest.mark.anyio
async def test_stripe_checkout_flow(client, auth_user, monkeypatch):
    class FakeSession:
        url = "https://stripe.test/checkout"
        customer = "cus_test"
        subscription = "sub_test"

    def fake_checkout(user, plan, success, cancel):
        assert plan == "basic"
        return FakeSession(), "price_test"

    monkeypatch.setattr("api.billing.router.create_checkout_session", fake_checkout)
    resp = await client.post("/billing/checkout", json={"plan": "basic"}, headers=auth_user["headers"])
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://stripe.test")


@pytest.mark.anyio
async def test_stripe_webhook_activates_subscription(client, auth_user, monkeypatch):
    settings.stripe_webhook_secret = "whsec_test"

    fake_event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": auth_user["user_id"],
                "subscription": "sub_test",
                "customer": "cus_test",
                "metadata": {"plan": "basic"},
            }
        },
    }

    def fake_construct_event(payload, sig, secret):
        return fake_event

    def fake_subscription_retrieve(_id):
        return {
            "current_period_end": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
            "items": {
                "data": [
                    {
                        "price": {
                            "id": "price_test",
                            "lookup_key": "rnd-basic",
                        }
                    }
                ]
            },
        }

    monkeypatch.setattr("stripe.Webhook.construct_event", fake_construct_event)
    monkeypatch.setattr("stripe.Subscription.retrieve", fake_subscription_retrieve)

    resp = await client.post("/billing/webhook", headers={"stripe-signature": "sig_test"}, content="{}")
    assert resp.status_code == 200

    sub = await client.get("/billing/subscription", headers=auth_user["headers"])
    assert sub.status_code == 200
    data = sub.json()
    assert data["status"] == "active"
