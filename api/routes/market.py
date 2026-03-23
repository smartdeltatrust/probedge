"""
api/routes/market.py
Endpoints de datos de mercado — FMP API.
"""
from __future__ import annotations

import math
import os
import sys

import requests
from fastapi import APIRouter, Depends, HTTPException, Query

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modules.data_provider.fmp import fetch_quote_history
from api.auth.dependencies import get_current_user
from api.auth.models import User
from api.core.config import get_settings
from api.core.rate_limit import rate_limit_dependency

router = APIRouter(prefix="/market", tags=["market"])

FMP_BASE = "https://financialmodelingprep.com"


def safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


@router.get("/{ticker}/quote", dependencies=[Depends(rate_limit_dependency)])
async def get_quote(
    ticker: str,
    current_user: User = Depends(get_current_user),
):
    settings = get_settings()
    try:
        resp = requests.get(
            f"{FMP_BASE}/stable/quote",
            params={"symbol": ticker.upper(), "apikey": settings.fmp_api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            raise HTTPException(status_code=404, detail=f"No hay datos para {ticker}")
        q = data[0]
        return {
            "ticker": q.get("symbol"),
            "name": q.get("name"),
            "price": safe_float(q.get("price")),
            "change": safe_float(q.get("change")),
            "change_pct": safe_float(q.get("changePercentage")),
            "volume": q.get("volume"),
            "day_high": safe_float(q.get("dayHigh")),
            "day_low": safe_float(q.get("dayLow")),
            "year_high": safe_float(q.get("yearHigh")),
            "year_low": safe_float(q.get("yearLow")),
            "market_cap": q.get("marketCap"),
            "prev_close": safe_float(q.get("previousClose")),
            "open": safe_float(q.get("open")),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error FMP: {exc}") from exc


@router.get("/{ticker}/history", dependencies=[Depends(rate_limit_dependency)])
async def get_history(
    ticker: str,
    days: int = Query(30, description="Días de historia a retornar (default 30, max 252)."),
    current_user: User = Depends(get_current_user),
):
    settings = get_settings()
    days = min(days, 252)
    try:
        df = fetch_quote_history(ticker.upper(), settings.fmp_api_key, days=days)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Sin historial para {ticker}")
        records = df.tail(days).to_dict(orient="records")
        for r in records:
            if hasattr(r.get("Date"), "isoformat"):
                r["Date"] = r["Date"].isoformat()
        return {
            "ticker": ticker.upper(),
            "days": len(records),
            "data": records,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error FMP: {exc}") from exc
