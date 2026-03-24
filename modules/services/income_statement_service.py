# src/services/income_statement_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pandas as pd

from modules.data_provider.fmp_income_statement_adapter import fetch_income_statement


DEFAULT_METRICS = ["revenue", "costOfRevenue", "grossProfit", "ebit", "ebitda"]
OPTIONAL_METRICS = ["operatingIncome", "netIncome"]

ALL_METRICS = DEFAULT_METRICS + OPTIONAL_METRICS


@dataclass(frozen=True)
class IncomeStatementPlotData:
    meta: Dict[str, Any]
    df: pd.DataFrame
    scale_div: float
    scale_label: str
    available_metrics: List[str]


def _choose_scale(max_abs_value: float) -> Tuple[float, str]:
    """
    Escala dinámica por compañía.
    Regla simple y robusta:
      >= 1e12 -> Trillions
      >= 1e9  -> Billions
      >= 1e6  -> Millions
      else    -> USD
    """
    if max_abs_value is None or pd.isna(max_abs_value):
        return 1.0, "USD"
    v = float(max_abs_value)
    if v >= 1e12:
        return 1e12, "USD Trillions"
    if v >= 1e9:
        return 1e9, "USD Billions"
    if v >= 1e6:
        return 1e6, "USD Millions"
    return 1.0, "USD"


def get_income_statement_plot_data(
    *,
    symbol: str,
    fmp_key: str,
    limit: int = 12,            # 12 quarters = ~3 años
    period: str = "quarter",    # default trimestral
) -> IncomeStatementPlotData:
    # Normalizar period para evitar inconsistencias (FY vs annual vs quarter)
    per = (period or "").strip().lower()
    if per in ("fy", "annual", "year", "yearly"):
        per = "annual"
    elif per in ("quarter", "q", "quarterly"):
        per = "quarter"
    else:
        per = "quarter"

    series = fetch_income_statement(symbol=symbol, api_key=fmp_key, limit=limit, period=per)

    rows: List[Dict[str, Any]] = []
    for it in series.items:
        r: Dict[str, Any] = {
            "date": it.get("date"),
            "fiscalYear": it.get("fiscalYear"),
            "period": it.get("period"),
        }
        for k in ALL_METRICS:
            r[k] = it.get(k)
        rows.append(r)

    df = pd.DataFrame(rows)

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")
        for k in ALL_METRICS:
            df[k] = pd.to_numeric(df[k], errors="coerce")

    max_abs = None
    if not df.empty:
        max_abs = float(df[ALL_METRICS].abs().max().max())

    div, label = _choose_scale(max_abs if max_abs is not None else 0.0)

    available: List[str] = []
    for k in ALL_METRICS:
        if not df.empty and df[k].notna().any():
            available.append(k)

    meta = {
        "symbol": series.symbol,
        "period": per,  # <-- period real normalizado
        "limit": int(limit),
        "updated_at_utc": series.updated_at_utc.isoformat(),
        "source": "FMP income-statement",
    }

    return IncomeStatementPlotData(
        meta=meta,
        df=df,
        scale_div=div,
        scale_label=label,
        available_metrics=available,
    )
