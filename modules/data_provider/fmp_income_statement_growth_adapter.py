from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

FMP_BASE = "https://financialmodelingprep.com/stable"


class FMPError(RuntimeError):
    pass


def _get_json(url: str, params: Dict[str, Any], timeout: int = 20) -> Any:
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code >= 400:
        raise FMPError(f"FMP HTTP {r.status_code}: {r.text[:250]}")
    return r.json()


def _coerce_scalar(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            return float(s.replace(",", ""))
        except Exception:
            return x
    return x


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (item or {}).items():
        out[k] = _coerce_scalar(v)
    return out


@dataclass(frozen=True)
class IncomeStatementGrowthSeries:
    symbol: str
    items: List[Dict[str, Any]]
    updated_at_utc: datetime

    def latest(self) -> Dict[str, Any]:
        return self.items[0] if self.items else {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "source": "FMP income-statement-growth",
            "series": list(self.items),
        }


def fetch_income_statement_growth(
    *,
    symbol: str,
    api_key: str,
    limit: int = 5,
    period: str = "FY",
    timeout: int = 20,
) -> IncomeStatementGrowthSeries:
    """
    GET /stable/income-statement-growth?symbol=XXX&limit=N&period=FY|quarter|annual|Q1...
    Retorna serie (lista) de registros, típicamente ordenados por fecha desc.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol es requerido")

    if limit is None:
        limit = 5
    limit = int(limit)
    if limit <= 0:
        limit = 5
    if limit > 1000:
        limit = 1000

    per = (period or "FY").strip()
    url = f"{FMP_BASE}/income-statement-growth"
    data = _get_json(
        url,
        {"symbol": sym, "limit": limit, "period": per, "apikey": api_key},
        timeout=timeout,
    )

    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                items.append(_normalize_item(row))

    sym_out = sym
    if items and isinstance(items[0].get("symbol"), str) and items[0].get("symbol"):
        sym_out = str(items[0].get("symbol")).strip().upper()

    return IncomeStatementGrowthSeries(
        symbol=sym_out,
        items=items,
        updated_at_utc=datetime.now(timezone.utc),
    )
