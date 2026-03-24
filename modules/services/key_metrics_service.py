# src/services/key_metrics_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from modules.data_provider.fmp_key_metrics_adapter import fetch_key_metrics_ttm, KeyMetricsTTM
# cambios dentro de src/services/key_metrics_service.py
import os


def _get_fmp_key() -> str:
    return (os.getenv("FMP_API_KEY") or "").strip()

def _inv_positive(x: Any) -> Optional[float]:
    """
    Inverso solo si x es numérico y > 0.
    Útil para P/E implícito (1/earningsYield) y P/FCF implícito (1/fcfYield).
    """
    try:
        v = float(x)
        if v <= 0:
            return None
        return 1.0 / v
    except Exception:
        return None


def _pick_present(d: Dict[str, Any], keys: list[str]) -> Dict[str, Any]:
    """
    Selecciona llaves si existen en el dict, conserva None si la llave existe.
    """
    out: Dict[str, Any] = {}
    for k in keys:
        if k in d:
            out[k] = d.get(k)
    return out


CATEGORY_MAP: Dict[str, list[str]] = {
    "A. Valoración y múltiplos": [
        "marketCap",
        "enterpriseValueTTM",
        "evToSalesTTM",
        "evToOperatingCashFlowTTM",
        "evToFreeCashFlowTTM",
        "evToEBITDATTM",
        "grahamNumberTTM",
        "grahamNetNetTTM",
    ],
    "B. Rentabilidad y retornos": [
        "returnOnAssetsTTM",
        "returnOnEquityTTM",
        "returnOnTangibleAssetsTTM",
        "returnOnInvestedCapitalTTM",
        "returnOnCapitalEmployedTTM",
        "operatingReturnOnAssetsTTM",
        "earningsYieldTTM",
        "freeCashFlowYieldTTM",
    ],
    "C. Solvencia y apalancamiento": [
        "netDebtToEBITDATTM",
        "currentRatioTTM",
        "workingCapitalTTM",
        "netCurrentAssetValueTTM",
    ],
    "D. Calidad de ganancias y flujo de caja": [
        "incomeQualityTTM",
        "freeCashFlowToEquityTTM",
        "freeCashFlowToFirmTTM",
    ],
    "E. Ciclo operativo y capital de trabajo": [
        "averageReceivablesTTM",
        "averagePayablesTTM",
        "averageInventoryTTM",
        "daysOfSalesOutstandingTTM",
        "daysOfPayablesOutstandingTTM",
        "daysOfInventoryOutstandingTTM",
        "operatingCycleTTM",
        "cashConversionCycleTTM",
    ],
    "F. Estructura de inversión": [
        "investedCapitalTTM",
        "capexToOperatingCashFlowTTM",
        "capexToDepreciationTTM",
        "capexToRevenueTTM",
        "intangiblesToTotalAssetsTTM",
    ],
    "G. Costos operativos": [
        "salesGeneralAndAdministrativeToRevenueTTM",
        "researchAndDevelopementToRevenueTTM",
        "stockBasedCompensationToRevenueTTM",
    ],
    "H. Estructura fiscal y financiera": [
        "taxBurdenTTM",
        "interestBurdenTTM",
    ],
}


@dataclass(frozen=True)
class KeyMetricsGroupedPayload:
    meta: Dict[str, Any]
    groups: Dict[str, Dict[str, Any]]
    derived: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "meta": dict(self.meta),
            "groups": {k: dict(v) for k, v in self.groups.items()},
            "derived": dict(self.derived),
        }



def build_key_metrics_grouped_payload(*, symbol: str) -> KeyMetricsGroupedPayload:
    """
    Construye payload agrupado A-H desde FMP key-metrics-ttm.
    Diseñado para ser consumido por UI y por LLM.
    """
    api_key = _get_fmp_key()
    if not api_key:
        raise RuntimeError("FMP_API_KEY no está configurada en el entorno.")

    km: KeyMetricsTTM = fetch_key_metrics_ttm(symbol=symbol, api_key=api_key)
    d = km.data

    groups: Dict[str, Dict[str, Any]] = {}
    for cat, keys in CATEGORY_MAP.items():
        groups[cat] = _pick_present(d, keys)

    ey = d.get("earningsYieldTTM")
    fcfy = d.get("freeCashFlowYieldTTM")

    pe_impl = _inv_positive(ey)
    pfcf_impl = _inv_positive(fcfy)

    derived = {
        "pe_implied_ttm": pe_impl,
        "p_fcf_implied_ttm": pfcf_impl,
        "pe_implied_is_interpretable": pe_impl is not None,
        "p_fcf_implied_is_interpretable": pfcf_impl is not None,
        "notes": (
            "P/E implícito y P/FCF implícito se calculan solo si los yields son positivos. "
            "Si el yield es <= 0, el múltiplo no es interpretable y se deja como None."
        ),
    }

    meta = {
        "symbol": km.symbol,
        "updated_at_utc": km.updated_at_utc.isoformat(),
        "source": "FMP key-metrics-ttm",
        "num_keys_total": len(d.keys()),
        "num_keys_non_null": sum(1 for _, v in d.items() if v is not None),
    }

    return KeyMetricsGroupedPayload(meta=meta, groups=groups, derived=derived)


def get_valuation_multiples_A(*, symbol: str) -> Dict[str, Any]:
    payload = build_key_metrics_grouped_payload(symbol=symbol)
    return payload.groups.get("A. Valoración y múltiplos", {})


def get_returns_metrics_B(*, symbol: str) -> Dict[str, Any]:
    payload = build_key_metrics_grouped_payload(symbol=symbol)
    return payload.groups.get("B. Rentabilidad y retornos", {})
