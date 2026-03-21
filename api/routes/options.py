"""
api/routes/options.py
Endpoints de opciones — Massive API + RND via Breeden-Litzenberger.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import sys, os, math, requests
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from modules.data_provider.massive import fetch_available_expiries, fetch_options_snapshot
from modules.utils import compute_rnd_from_calls
from api.core.config import get_settings

router = APIRouter(prefix="/options", tags=["options"])


def clean_record(row: dict) -> dict:
    """Reemplaza NaN/Inf con None para serialización JSON limpia."""
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in row.items()
    }


def get_spot_price(ticker: str, fmp_api_key: str) -> float:
    """Obtiene el precio spot actual via FMP /stable/quote."""
    url = f"https://financialmodelingprep.com/stable/quote"
    resp = requests.get(url, params={"symbol": ticker, "apikey": fmp_api_key}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"FMP no devolvió datos para {ticker}")
    return float(data[0]["price"])


@router.get("/{ticker}/expiries")
async def get_expiries(ticker: str):
    """Lista los vencimientos disponibles para un ticker."""
    settings = get_settings()
    try:
        expiries = fetch_available_expiries(ticker.upper(), settings.massive_api_key)
        return {"ticker": ticker.upper(), "expiries": expiries, "count": len(expiries)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error Massive API: {e}")


@router.get("/{ticker}/chain")
async def get_options_chain(
    ticker: str,
    expiration: Optional[str] = Query(None, description="Fecha YYYY-MM-DD."),
    limit: int = Query(50, description="Máximo de contratos a retornar.")
):
    """Retorna la cadena de opciones para un ticker y vencimiento."""
    settings = get_settings()
    try:
        if not expiration:
            expiries = fetch_available_expiries(ticker.upper(), settings.massive_api_key)
            if not expiries:
                raise HTTPException(status_code=404, detail=f"No hay vencimientos para {ticker}")
            expiration = expiries[0]

        df = fetch_options_snapshot(ticker.upper(), expiration, settings.massive_api_key)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Sin datos para {ticker} exp {expiration}")

        records = [clean_record(r) for r in df.head(limit).to_dict(orient="records")]
        return {
            "ticker": ticker.upper(),
            "expiration": expiration,
            "count": len(df),
            "returned": len(records),
            "columns": df.columns.tolist(),
            "data": records,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error: {e}")


@router.get("/{ticker}/rnd")
async def get_rnd(
    ticker: str,
    expiration: Optional[str] = Query(None, description="Fecha YYYY-MM-DD."),
    r_annual: float = Query(0.045, description="Tasa libre de riesgo anual."),
    q_annual: float = Query(0.0, description="Dividend yield anual."),
    oi_min: int = Query(50, description="Open interest mínimo para filtrar contratos."),
    n_grid: int = Query(400, description="Puntos en el grid de precios."),
):
    """
    Calcula la Risk-Neutral Density (RND) via Breeden-Litzenberger.
    Usa la cadena de opciones de Massive y el precio spot de FMP.
    """
    settings = get_settings()
    try:
        # 1. Vencimiento
        if not expiration:
            expiries = fetch_available_expiries(ticker.upper(), settings.massive_api_key)
            if not expiries:
                raise HTTPException(status_code=404, detail=f"No hay vencimientos para {ticker}")
            expiration = expiries[0]

        # 2. Spot price via FMP
        try:
            spot = get_spot_price(ticker.upper(), settings.fmp_api_key)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error FMP (spot): {e}")

        # 3. Cadena de opciones via Massive
        df = fetch_options_snapshot(ticker.upper(), expiration, settings.massive_api_key)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Sin datos de opciones para {ticker} exp {expiration}")

        # 4. Calcular RND
        valuation_date = pd.Timestamp.today().normalize()
        expiry_date = pd.Timestamp(expiration)
        tau_days = (expiry_date - valuation_date).days

        if tau_days <= 0:
            raise HTTPException(status_code=422, detail=f"El vencimiento {expiration} ya pasó o es hoy.")

        try:
            price_grid, rnd_values = compute_rnd_from_calls(
                options_df=df.rename(columns={"contract_type": "option_type", "last_price": "last_close"}),
                spot=spot,
                valuation_date=valuation_date,
                expiry_date=expiry_date,
                r_annual=r_annual,
                q_annual=q_annual,
                oi_min=oi_min,
                n_grid=n_grid,
            )
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Error calculando RND: {e}")

        # 5. Limpiar NaN/Inf y serializar
        def safe_float(v):
            if isinstance(v, (float, np.floating)):
                return None if (math.isnan(v) or math.isinf(v)) else float(v)
            return float(v)

        return {
            "ticker": ticker.upper(),
            "expiration": expiration,
            "spot": spot,
            "tau_days": tau_days,
            "r_annual": r_annual,
            "n_grid": len(price_grid),
            "price_grid": [safe_float(v) for v in price_grid],
            "rnd": [safe_float(v) for v in rnd_values],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {e}")


@router.get("/{ticker}/probabilities")
async def get_probabilities(
    ticker: str,
    expiration: Optional[str] = Query(None),
    price_target: float = Query(..., description="Precio objetivo para calcular P(S_T > target) y P(S_T < target)"),
    r_annual: float = Query(0.045),
    q_annual: float = Query(0.0),
    oi_min: int = Query(50),
):
    """
    Calcula probabilidades risk-neutral: P(S_T > target) y P(S_T < target)
    a partir de la RND de Breeden-Litzenberger.
    """
    settings = get_settings()
    try:
        if not expiration:
            expiries = fetch_available_expiries(ticker.upper(), settings.massive_api_key)
            if not expiries:
                raise HTTPException(status_code=404, detail=f"No hay vencimientos para {ticker}")
            expiration = expiries[0]

        spot = get_spot_price(ticker.upper(), settings.fmp_api_key)
        df = fetch_options_snapshot(ticker.upper(), expiration, settings.massive_api_key)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Sin datos para {ticker} exp {expiration}")

        valuation_date = pd.Timestamp.today().normalize()
        expiry_date = pd.Timestamp(expiration)
        tau_days = (expiry_date - valuation_date).days

        if tau_days <= 0:
            raise HTTPException(status_code=422, detail=f"Vencimiento {expiration} ya pasó.")

        price_grid, rnd_values = compute_rnd_from_calls(
            options_df=df.rename(columns={"contract_type": "option_type", "last_price": "last_close"}),
            spot=spot,
            valuation_date=valuation_date,
            expiry_date=expiry_date,
            r_annual=r_annual,
            q_annual=q_annual,
            oi_min=oi_min,
        )

        pg = np.array(price_grid)
        rnd = np.array(rnd_values)

        # Probabilidades via integración numérica
        mask_above = pg >= price_target
        mask_below = pg < price_target

        p_above = float(np.trapezoid(rnd[mask_above], pg[mask_above])) if mask_above.any() else 0.0
        p_below = float(np.trapezoid(rnd[mask_below], pg[mask_below])) if mask_below.any() else 0.0

        return {
            "ticker": ticker.upper(),
            "expiration": expiration,
            "spot": spot,
            "price_target": price_target,
            "tau_days": tau_days,
            "p_above": round(p_above, 6),
            "p_below": round(p_below, 6),
            "p_above_pct": round(p_above * 100, 2),
            "p_below_pct": round(p_below * 100, 2),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")
