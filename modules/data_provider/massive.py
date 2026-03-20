"""
modules/data_provider/massive.py
Provider de opciones via Massive (ex-Polygon.io) API.
"""
from __future__ import annotations

import time
import requests
import pandas as pd


_BASE = "https://api.polygon.io"


def _get(url: str, params: dict, retries: int = 3, backoff: float = 1.0) -> dict:
    """GET con reintentos. Lanza RuntimeError si falla tras todos los intentos."""
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
            raise RuntimeError(f"Error HTTP en Massive API: {e}") from e
    raise RuntimeError(f"Timeout tras {retries} intentos en Massive API") from last_err


def _paginate(first_url: str, params: dict) -> list[dict]:
    """Sigue next_url hasta agotar resultados. Devuelve lista de resultados."""
    all_results: list[dict] = []
    api_key = params.get("apiKey", "")
    url: str | None = first_url
    current_params = params
    while url:
        data = _get(url, current_params)
        results = data.get("results") or []
        all_results.extend(results)
        next_url = data.get("next_url")
        if next_url:
            # next_url ya trae query params pero NO incluye apiKey
            sep = "&" if "?" in next_url else "?"
            url = f"{next_url}{sep}apiKey={api_key}" if api_key else next_url
            current_params = {}
        else:
            url = None
    return all_results


def fetch_options_chain(ticker: str, expiry: str, api_key: str) -> pd.DataFrame:
    """
    Descarga la cadena de opciones para un ticker y vencimiento.

    Parámetros
    ----------
    ticker : str  Símbolo del subyacente (ej. 'AAPL')
    expiry : str  Fecha de vencimiento en formato 'YYYY-MM-DD'
    api_key : str  Clave de API de Massive/Polygon.io

    Devuelve
    --------
    pd.DataFrame con columnas compatibles con clean_options_chain():
        contract, option_type, strike, bid, ask, last_close, volume,
        open_interest, iv, delta, gamma, theta, vega
    """
    url = f"{_BASE}/v3/snapshot/options/{ticker.upper()}"
    params = {
        "expiration_date": expiry,
        "limit": 250,
        "apiKey": api_key,
    }

    try:
        results = _paginate(url, params)
    except RuntimeError as e:
        raise RuntimeError(f"No se pudo descargar la cadena de opciones para {ticker}: {e}") from e

    if not results:
        return pd.DataFrame()

    rows = []
    for item in results:
        details = item.get("details", {})
        greeks = item.get("greeks", {})
        day = item.get("day", {})
        last_quote = item.get("last_quote", {})

        row = {
            "contract": details.get("ticker", ""),
            "option_type": details.get("contract_type", "").lower(),
            "strike": details.get("strike_price"),
            "bid": last_quote.get("bid") if last_quote else None,
            "ask": last_quote.get("ask") if last_quote else None,
            "last_close": day.get("close"),
            "volume": day.get("volume"),
            "open_interest": item.get("open_interest"),
            "iv": item.get("implied_volatility"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            # campos extra de day
            "day_open": day.get("open"),
            "day_high": day.get("high"),
            "day_low": day.get("low"),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    # Convertir numéricos
    num_cols = [
        "strike", "bid", "ask", "last_close", "volume",
        "open_interest", "iv", "delta", "gamma", "theta", "vega",
        "day_open", "day_high", "day_low",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.reset_index(drop=True)


def fetch_available_expiries(ticker: str, api_key: str) -> list[str]:
    """
    Devuelve lista de fechas de vencimiento disponibles para el ticker,
    ordenadas ascendentemente (YYYY-MM-DD).

    Parámetros
    ----------
    ticker : str
    api_key : str

    Devuelve
    --------
    list[str]  Ej. ['2025-06-20', '2025-07-18', ...]
    """
    url = f"{_BASE}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": ticker.upper(),
        "expired": "false",
        "limit": 1000,
        "apiKey": api_key,
    }

    try:
        results = _paginate(url, params)
    except RuntimeError as e:
        raise RuntimeError(f"No se pudieron obtener los vencimientos para {ticker}: {e}") from e

    expiry_set: set[str] = set()
    for contract in results:
        exp = contract.get("expiration_date")
        if exp:
            expiry_set.add(str(exp))

    try:
        return sorted(expiry_set, key=pd.to_datetime)
    except Exception:
        return sorted(expiry_set)


def fetch_options_snapshot(ticker: str, expiry: str, api_key: str) -> pd.DataFrame:
    """
    Igual que fetch_options_chain pero expone columnas adicionales de snapshot:
    strike, contract_type, bid, ask, last_price, open_interest, iv,
    delta, gamma, theta, vega, volume.

    Parámetros
    ----------
    ticker : str
    expiry : str  'YYYY-MM-DD'
    api_key : str

    Devuelve
    --------
    pd.DataFrame
    """
    df = fetch_options_chain(ticker, expiry, api_key)
    if df.empty:
        return df

    # Renombrar para que coincida con la nomenclatura de snapshot
    rename = {
        "option_type": "contract_type",
        "last_close": "last_price",
    }
    df = df.rename(columns=rename)

    snapshot_cols = [
        "strike", "contract_type", "bid", "ask", "last_price",
        "open_interest", "iv", "delta", "gamma", "theta", "vega", "volume",
    ]
    available = [c for c in snapshot_cols if c in df.columns]
    return df[available].reset_index(drop=True)
