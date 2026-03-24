# src/adapters/fmp_company_screener_adapter.py
from __future__ import annotations

from typing import Any, Dict, List
import time
import requests

FMP_BASE = "https://financialmodelingprep.com/stable"


class FMPError(RuntimeError):
    pass


def _get_json(url: str, params: Dict[str, Any], timeout: int = 25) -> Any:
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code == 429:
        time.sleep(0.7)
        r = requests.get(url, params=params, timeout=timeout)

    if r.status_code >= 400:
        raise FMPError(f"FMP HTTP {r.status_code}: {r.text[:250]}")
    return r.json()


def fetch_company_screener(
    *,
    sector: str,
    industry: str,
    api_key: str,
    limit: int = 80,
    timeout: int = 25,
) -> List[Dict[str, Any]]:
    sec = (sector or "").strip()
    ind = (industry or "").strip()
    if not sec or not ind:
        return []

    url = f"{FMP_BASE}/company-screener"
    params = {"sector": sec, "industry": ind, "limit": int(limit), "apikey": api_key}
    data = _get_json(url, params, timeout=timeout)

    if not isinstance(data, list):
        return []

    out: List[Dict[str, Any]] = []
    for it in data:
        if isinstance(it, dict):
            sym = str(it.get("symbol") or "").strip().upper()
            if sym:
                d = dict(it)
                d["symbol"] = sym
                out.append(d)
    return out