from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from modules.data_provider.fmp_income_statement_growth_adapter import (
    fetch_income_statement_growth,
    IncomeStatementGrowthSeries,
)


def _pick(d: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in keys:
        if k in d:
            out[k] = d.get(k)
    return out


def _to_pct(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x) * 100.0
    except Exception:
        return None


GROWTH_GROUPS: Dict[str, List[str]] = {
    "A. Crecimiento de ingresos y margen bruto": [
        "growthRevenue",
        "growthCostOfRevenue",
        "growthGrossProfit",
        "growthGrossProfitRatio",
    ],
    "B. Crecimiento de inversión operativa y eficiencia": [
        "growthResearchAndDevelopmentExpenses",
        "growthSellingAndMarketingExpenses",
        "growthGeneralAndAdministrativeExpenses",
        "growthOperatingExpenses",
        "growthCostAndExpenses",
        "growthDepreciationAndAmortization",
    ],
    "C. Crecimiento de rentabilidad operativa": [
        "growthEBITDA",
        "growthEBIT",
        "growthOperatingIncome",
    ],
    "D. Crecimiento de utilidad neta, EPS y capital accionario": [
        "growthNetIncome",
        "growthNetIncomeFromContinuingOperations",
        "growthEPS",
        "growthEPSDiluted",
        "growthWeightedAverageShsOut",
        "growthWeightedAverageShsOutDil",
    ],
    "E. Componentes no operativos e impuestos": [
        "growthInterestIncome",
        "growthInterestExpense",
        "growthNetInterestIncome",
        "growthNonOperatingIncomeExcludingInterest",
        "growthTotalOtherIncomeExpensesNet",
        "growthIncomeBeforeTax",
        "growthIncomeTaxExpense",
    ],
}


@dataclass(frozen=True)
class IncomeGrowthPayload:
    meta: Dict[str, Any]
    latest: Dict[str, Any]
    groups_latest: Dict[str, Dict[str, Any]]
    trend: List[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "meta": dict(self.meta),
            "latest": dict(self.latest),
            "groups_latest": {k: dict(v) for k, v in self.groups_latest.items()},
            "trend": list(self.trend),
        }


def build_income_growth_payload(
    *,
    symbol: str,
    fmp_key: str,
    limit: int = 5,
    period: str = "FY",
) -> IncomeGrowthPayload:
    series: IncomeStatementGrowthSeries = fetch_income_statement_growth(
        symbol=symbol,
        api_key=fmp_key,
        limit=limit,
        period=period,
    )

    latest = series.latest() or {}
    groups_latest: Dict[str, Dict[str, Any]] = {}
    for cat, keys in GROWTH_GROUPS.items():
        groups_latest[cat] = _pick(latest, keys)

    # Trend compacto para LLM: solo algunas métricas clave por periodo
    trend_keys = [
        "date",
        "fiscalYear",
        "period",
        "growthRevenue",
        "growthGrossProfit",
        "growthOperatingIncome",
        "growthEBITDA",
        "growthNetIncome",
        "growthEPS",
        "growthIncomeTaxExpense",
    ]
    trend: List[Dict[str, Any]] = []
    for row in series.items[: max(1, int(limit))]:
        t = _pick(row, trend_keys)
        trend.append(t)

    meta = {
        "symbol": series.symbol,
        "updated_at_utc": series.updated_at_utc.isoformat(),
        "source": "FMP income-statement-growth",
        "period": period,
        "limit": int(limit),
        "num_rows": len(series.items),
    }

    return IncomeGrowthPayload(
        meta=meta,
        latest=latest,
        groups_latest=groups_latest,
        trend=trend,
    )


def get_income_growth_latest_groups(
    *,
    symbol: str,
    fmp_key: str,
    limit: int = 5,
    period: str = "FY",
) -> Dict[str, Dict[str, Any]]:
    payload = build_income_growth_payload(symbol=symbol, fmp_key=fmp_key, limit=limit, period=period)
    return payload.groups_latest


def get_income_growth_trend(
    *,
    symbol: str,
    fmp_key: str,
    limit: int = 5,
    period: str = "FY",
) -> List[Dict[str, Any]]:
    payload = build_income_growth_payload(symbol=symbol, fmp_key=fmp_key, limit=limit, period=period)
    return payload.trend
