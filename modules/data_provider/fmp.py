"""
modules/data_provider/fmp.py
Provider de OHLC histórico via Financial Modeling Prep (FMP) API.
"""
from __future__ import annotations

import time
import requests
import pandas as pd


_BASE = "https://financialmodelingprep.com"


def _get(url: str, params: dict, retries: int = 3, backoff: float = 1.0) -> dict | list:
    """GET con reintentos. Devuelve el JSON parseado."""
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff * (attempt + 1)))
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Error HTTP en FMP API: {e}") from e
    raise RuntimeError(f"Timeout tras {retries} intentos en FMP API") from last_err


def fetch_quote_history(
    ticker: str,
    api_key: str,
    days: int = 252,
) -> pd.DataFrame:
    """
    Descarga el historial de precios OHLCV desde FMP y devuelve los últimos N días.

    Parámetros
    ----------
    ticker : str    Símbolo del activo (ej. 'AAPL')
    api_key : str   Clave de API de FMP
    days : int      Número de días de negociación a conservar (default=252 ≈ 1 año)

    Devuelve
    --------
    pd.DataFrame con columnas: Date, Open, High, Low, Close, Volume
        - Date: pd.Timestamp, ordenado ascendentemente
        - las demás son float / int
    """
    url = f"{_BASE}/stable/historical-price-eod/full"
    params = {
        "symbol": ticker.upper(),
        "apikey": api_key,
    }

    try:
        data = _get(url, params)
    except RuntimeError as e:
        raise RuntimeError(f"No se pudo descargar el historial de {ticker} desde FMP: {e}") from e

    # FMP puede devolver {"historical": [...]} o directamente una lista
    if isinstance(data, dict):
        records = data.get("historical") or data.get("data") or []
    elif isinstance(data, list):
        records = data
    else:
        records = []

    if not records:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    df = pd.DataFrame(records)

    # Normalizar nombres de columnas (FMP puede usar camelCase o snake_case)
    col_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if col_lower == "date":
            col_map[col] = "Date"
        elif col_lower == "open":
            col_map[col] = "Open"
        elif col_lower == "high":
            col_map[col] = "High"
        elif col_lower == "low":
            col_map[col] = "Low"
        elif col_lower in ("close", "adjclose", "adj_close", "adjusted_close"):
            if "Close" not in col_map.values():
                col_map[col] = "Close"
        elif col_lower == "volume":
            col_map[col] = "Volume"
    df = df.rename(columns=col_map)

    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"La respuesta de FMP para {ticker} no contiene las columnas: {missing}. "
            f"Columnas disponibles: {list(df.columns)}"
        )

    # Parsear fechas y ordenar ascendentemente
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Convertir numéricos
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Filtrar los últimos N días de negociación
    if days > 0 and len(df) > days:
        df = df.tail(days).reset_index(drop=True)

    return df[required]
