# src/adapters/fmp_adapter.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from modules.data_provider.http_client import get_json

FMP_BASE_URL = "https://financialmodelingprep.com/stable"


def _get_fmp_key() -> str:
    # Asegúrate que tu .env y Render usen el MISMO nombre
    return (os.getenv("FMP_API_KEY") or "").strip()


def _build_url(path: str, params: Dict[str, Any]) -> str:
    # Limpia None / "" para evitar querystrings sucios
    clean: Dict[str, Any] = {}
    for k, v in params.items():
        if v is None or v == "":
            continue
        clean[k] = v
    return f"{FMP_BASE_URL}/{path}?{urlencode(clean)}"


def search_symbols_fmp(
    query: str,
    *,
    limit: int = 25,
    exchange: Optional[str] = None,
) -> List[Dict[str, Any]]:
    api_key = _get_fmp_key()
    if not api_key:
        return []

    q = (query or "").strip()
    if not q:
        return []

    lim = max(1, min(int(limit), 50))  # FMP suele recomendar límites razonables
    url = _build_url(
        "search-symbol",
        {"query": q, "limit": lim, "exchange": exchange, "apikey": api_key},
    )
    data = get_json(url)
    return data if isinstance(data, list) else []


def get_profile_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    api_key = _get_fmp_key()
    s = (symbol or "").strip().upper()
    if not api_key or not s:
        return None

    url = _build_url("profile", {"symbol": s, "apikey": api_key})
    data = get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def get_profile_by_cik(cik: str) -> Optional[Dict[str, Any]]:
    api_key = _get_fmp_key()
    c = (cik or "").strip()
    if not api_key or not c:
        return None

    url = _build_url("profile-cik", {"cik": c, "apikey": api_key})
    data = get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def get_revenue_product_segmentation(
    symbol: str,
    *,
    period: str = "annual",
    structure: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    api_key = _get_fmp_key()
    s = (symbol or "").strip().upper()
    if not api_key or not s:
        return []

    p = (period or "annual").strip().lower()
    if p not in ("annual", "quarter"):
        p = "annual"

    params: Dict[str, Any] = {"symbol": s, "period": p, "apikey": api_key}
    if structure:
        params["structure"] = structure
    if limit is not None:
        params["limit"] = max(1, min(int(limit), 1000))

    url = _build_url("revenue-product-segmentation", params)
    data = get_json(url)

    return data if isinstance(data, list) else []


def get_historical_market_capitalization(
    symbol: str,
    *,
    limit: int = 5000,
    date_from: Optional[str] = None,  # "YYYY-MM-DD"
    date_to: Optional[str] = None,    # "YYYY-MM-DD"
) -> List[Dict[str, Any]]:
    api_key = _get_fmp_key()
    s = (symbol or "").strip().upper()
    if not api_key or not s:
        return []

    lim = max(1, min(int(limit), 5000))

    url = _build_url(
        "historical-market-capitalization",
        {
            "symbol": s,
            "limit": lim,
            "from": date_from,
            "to": date_to,
            "apikey": api_key,
        },
    )

    data = get_json(url)
    return data if isinstance(data, list) else []


def get_quote_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    api_key = _get_fmp_key()
    s = (symbol or "").strip().upper()
    if not api_key or not s:
        return None

    url = _build_url("quote", {"symbol": s, "apikey": api_key})
    data = get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def get_stock_price_change(symbol: str) -> Optional[Dict[str, Any]]:
    api_key = _get_fmp_key()
    s = (symbol or "").strip().upper()
    if not api_key or not s:
        return None

    url = _build_url("stock-price-change", {"symbol": s, "apikey": api_key})
    data = get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def get_shares_float(symbol: str) -> Optional[Dict[str, Any]]:
    api_key = _get_fmp_key()
    s = (symbol or "").strip().upper()
    if not api_key or not s:
        return None

    url = _build_url("shares-float", {"symbol": s, "apikey": api_key})
    data = get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None
