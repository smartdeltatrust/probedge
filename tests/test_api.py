"""
tests/test_api.py
Tests de integración para todos los endpoints FastAPI.
Requiere: API keys en .env y servicio externo (Massive + FMP).
"""
import pytest
import math
import numpy as np
from httpx import AsyncClient, ASGITransport
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


# --- /health ---
@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


# --- /options/expiries ---
@pytest.mark.anyio
async def test_expiries_returns_list(client):
    r = await client.get(f"/options/{TICKER}/expiries")
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == TICKER
    assert isinstance(data["expiries"], list)
    assert data["count"] > 0


# --- /options/chain ---
@pytest.mark.anyio
async def test_chain_returns_contracts(client):
    r = await client.get(f"/options/{TICKER}/chain", params={"expiration": EXPIRY, "limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == TICKER
    assert data["count"] > 0
    assert len(data["data"]) <= 10
    # Verificar columnas clave
    first = data["data"][0]
    assert "strike" in first
    assert "contract_type" in first or "option_type" in first


# --- /options/rnd ---
@pytest.mark.anyio
async def test_rnd_is_valid_density(client):
    r = await client.get(f"/options/{TICKER}/rnd", params={"expiration": EXPIRY})
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == TICKER
    assert data["spot"] > 0
    assert data["tau_days"] > 0
    assert data["n_grid"] > 0

    pg = [v for v in data["price_grid"] if v is not None]
    rnd = [v for v in data["rnd"] if v is not None]
    assert len(pg) == len(rnd)
    assert len(pg) > 10

    # Integral ≈ 1.0 (densidad normalizada)
    integral = np.trapezoid(rnd, pg)
    assert abs(integral - 1.0) < 0.05, f"Integral = {integral:.4f}, esperada ≈ 1.0"

    # Sin valores negativos
    assert all(v >= 0 for v in rnd if v is not None)


# --- /market/quote ---
@pytest.mark.anyio
async def test_market_quote(client):
    r = await client.get(f"/market/{TICKER}/quote")
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == TICKER
    assert data["price"] > 0
    assert data["name"] is not None


# --- /market/history ---
@pytest.mark.anyio
async def test_market_history(client):
    r = await client.get(f"/market/{TICKER}/history", params={"days": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == TICKER
    assert data["days"] <= 5
    assert len(data["data"]) > 0
    first = data["data"][0]
    assert "Close" in first
    assert "Date" in first
