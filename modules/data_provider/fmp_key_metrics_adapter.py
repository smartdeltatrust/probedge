# src/adapters/fmp_key_metrics_adapter.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
    """
    Normaliza valores escalares sin ser agresivo.

    - None -> None
    - int/float -> se deja tal cual
    - str numérica -> float
    - resto -> se deja tal cual (por si FMP mete strings no numéricas)
    """
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            # FMP normalmente devuelve números como num, pero si llegaran como string, lo normalizamos
            return float(s.replace(",", ""))
        except Exception:
            return x
    return x


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Devuelve un dict con todos los campos posibles del endpoint,
    normalizando escalares cuando sea posible.
    """
    out: Dict[str, Any] = {}
    for k, v in (item or {}).items():
        out[k] = _coerce_scalar(v)
    return out


@dataclass(frozen=True)
class KeyMetricsTTM:
    """
    Contenedor robusto: guarda todo el payload de FMP sin perder campos.

    data:
      - incluye todos los campos devueltos por FMP (ya normalizados),
        incluyendo "symbol" si viene.
    """
    symbol: str
    data: Dict[str, Any]
    updated_at_utc: datetime

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        # Útil para pasar directo a services/LLM
        return {
            "symbol": self.symbol,
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "metrics_ttm": dict(self.data),
            "source": "FMP key-metrics-ttm",
        }


def fetch_key_metrics_ttm(*, symbol: str, api_key: str, timeout: int = 20) -> KeyMetricsTTM:
    """
    Extrae TODOS los campos disponibles del endpoint:
      GET /stable/key-metrics-ttm?symbol=XXX

    Retorna un KeyMetricsTTM con:
      - symbol normalizado
      - data con todo el payload (campos completos)
      - updated_at_utc
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol es requerido")

    url = f"{FMP_BASE}/key-metrics-ttm"
    data = _get_json(url, {"symbol": sym, "apikey": api_key}, timeout=timeout)

    if not isinstance(data, list) or not data:
        # Endpoint respondió pero sin datos
        return KeyMetricsTTM(
            symbol=sym,
            data={},
            updated_at_utc=datetime.now(timezone.utc),
        )

    item_raw = data[0] if isinstance(data[0], dict) else {}
    item = _normalize_item(item_raw)

    # Si FMP trae symbol dentro del objeto, lo respetamos, si no usamos el solicitado
    sym_out = str(item.get("symbol") or sym).strip().upper() or sym

    return KeyMetricsTTM(
        symbol=sym_out,
        data=item,
        updated_at_utc=datetime.now(timezone.utc),
    )
