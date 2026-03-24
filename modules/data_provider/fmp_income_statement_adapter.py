# src/adapters/fmp_income_statement_adapter.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

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


def _normalize_period(period: str) -> str:
    """
    Unifica inputs comunes para evitar inconsistencias entre módulos.
    FMP acepta 'quarter' y 'annual'. En tu proyecto a veces aparece 'FY'.
    """
    p = (period or "").strip()
    if not p:
        return "quarter"
    pl = p.lower()
    if pl in ("fy", "fY".lower(), "fiscal", "fiscalyear"):
        return "annual"
    if pl in ("annual", "quarter"):
        return pl
    # Si te pasan Q1/Q2/Q3/Q4 o FY, lo respetamos como venga,
    # pero tu UI debe preferir annual/quarter.
    return p


@dataclass(frozen=True)
class IncomeStatementSeries:
    symbol: str
    items: List[Dict[str, Any]]
    updated_at_utc: datetime

    def latest(self) -> Dict[str, Any]:
        return self.items[0] if self.items else {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "source": "FMP income-statement",
            "series": list(self.items),
        }


def fetch_income_statement(
    *,
    symbol: str,
    api_key: str,
    limit: int = 12,           # <-- default: 3 años trimestral (12 quarters)
    period: str = "quarter",   # <-- default: trimestral
    timeout: int = 20,
) -> IncomeStatementSeries:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol es requerido")

    limit = int(limit)
    if limit <= 0:
        limit = 12
    if limit > 1000:
        limit = 1000

    per = _normalize_period(period)
    url = f"{FMP_BASE}/income-statement"

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

    return IncomeStatementSeries(
        symbol=sym_out,
        items=items,
        updated_at_utc=datetime.now(timezone.utc),
    )
