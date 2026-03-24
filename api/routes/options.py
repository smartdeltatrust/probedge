"""
api/routes/options.py
Endpoints de opciones — tastytrade + dxFeed + RND via Breeden-Litzenberger.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modules.data_provider.tastytrade_options import (
    fetch_available_expiries,
    fetch_options_snapshot,
    get_spot_price,
    _get_tt_token,
)
from modules.utils import compute_rnd_from_calls
from api.auth.dependencies import get_current_user
from api.auth.models import User
from api.core.config import get_settings
from api.core.rate_limit import rate_limit_dependency
from api.credits.dependencies import require_credits_dependency

router = APIRouter(prefix="/options", tags=["options"])


def clean_record(row: dict) -> dict:
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in row.items()
    }


def _safe_float(value):
    if isinstance(value, (float, np.floating)):
        return None if (math.isnan(value) or math.isinf(value)) else float(value)
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover
        return None


async def _prepare_rnd_data(
    ticker: str,
    expiration: Optional[str],
    r_annual: float,
    q_annual: float,
    oi_min: int,
    n_grid: int,
):
    tt_token = _get_tt_token()

    if not expiration:
        expiries = fetch_available_expiries(ticker.upper(), tt_token)
        if not expiries:
            raise HTTPException(status_code=404, detail=f"No hay vencimientos para {ticker}")
        expiration = expiries[0]

    try:
        spot = get_spot_price(ticker.upper(), tt_token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error dxFeed (spot): {exc}") from exc

    df = fetch_options_snapshot(ticker.upper(), expiration, tt_token)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"Sin datos para {ticker} exp {expiration}")

    valuation_date = pd.Timestamp.today().normalize()
    expiry_date = pd.Timestamp(expiration)
    tau_days = (expiry_date - valuation_date).days
    if tau_days <= 0:
        raise HTTPException(status_code=422, detail=f"El vencimiento {expiration} ya pasó o es hoy.")

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

    metadata = {
        "ticker": ticker.upper(),
        "expiration": expiration,
        "spot": spot,
        "tau_days": tau_days,
        "r_annual": r_annual,
        "n_grid": len(price_grid),
    }
    return metadata, price_grid, rnd_values


@router.get("/{ticker}/expiries", dependencies=[Depends(rate_limit_dependency)])
async def get_expiries(
    ticker: str,
    current_user: User = Depends(get_current_user),
):
    try:
        tt_token = _get_tt_token()
        expiries = fetch_available_expiries(ticker.upper(), tt_token)
        return {"ticker": ticker.upper(), "expiries": expiries, "count": len(expiries)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error tastytrade API: {exc}") from exc


@router.get("/{ticker}/chain", dependencies=[Depends(rate_limit_dependency)])
async def get_options_chain(
    ticker: str,
    expiration: Optional[str] = Query(None, description="Fecha YYYY-MM-DD."),
    limit: int = Query(50, description="Máximo de contratos a retornar."),
    current_user: User = Depends(get_current_user),
):
    try:
        tt_token = _get_tt_token()
        if not expiration:
            expiries = fetch_available_expiries(ticker.upper(), tt_token)
            if not expiries:
                raise HTTPException(status_code=404, detail=f"No hay vencimientos para {ticker}")
            expiration = expiries[0]

        df = fetch_options_snapshot(ticker.upper(), expiration, tt_token)
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
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error: {exc}") from exc


@router.get(
    "/{ticker}/rnd",
    dependencies=[Depends(rate_limit_dependency), Depends(require_credits_dependency(25, "Analysis RND"))],
)
async def get_rnd(
    ticker: str,
    expiration: Optional[str] = Query(None, description="Fecha YYYY-MM-DD."),
    r_annual: float = Query(0.045, description="Tasa libre de riesgo anual."),
    q_annual: float = Query(0.0, description="Dividend yield anual."),
    oi_min: int = Query(50, description="Open interest mínimo para filtrar contratos."),
    n_grid: int = Query(400, description="Puntos en el grid de precios."),
    current_user: User = Depends(get_current_user),
):
    try:
        metadata, price_grid, rnd_values = await _prepare_rnd_data(
            ticker, expiration, r_annual, q_annual, oi_min, n_grid
        )
        return {
            **metadata,
            "price_grid": [_safe_float(v) for v in price_grid],
            "rnd": [_safe_float(v) for v in rnd_values],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {exc}") from exc


@router.get(
    "/{ticker}/rnd/preview",
    dependencies=[Depends(rate_limit_dependency)],
)
async def rnd_preview(
    ticker: str,
    expiration: Optional[str] = Query(None, description="Fecha YYYY-MM-DD."),
):
    try:
        metadata, price_grid, rnd_values = await _prepare_rnd_data(
            ticker, expiration, r_annual=0.045, q_annual=0.0, oi_min=50, n_grid=200
        )
        cutoff = max(1, len(price_grid) // 2)
        return {
            "ticker": metadata["ticker"],
            "expiration": metadata["expiration"],
            "price_grid": [_safe_float(v) for v in price_grid[:cutoff]],
            "rnd": [_safe_float(v) for v in rnd_values[:cutoff]],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {exc}") from exc


@router.get(
    "/{ticker}/probabilities",
    dependencies=[Depends(rate_limit_dependency), Depends(require_credits_dependency(10, "Probabilities"))],
)
async def get_probabilities(
    ticker: str,
    expiration: Optional[str] = Query(None),
    price_target: float = Query(..., description="Precio objetivo"),
    r_annual: float = Query(0.045),
    q_annual: float = Query(0.0),
    oi_min: int = Query(50),
    current_user: User = Depends(get_current_user),
):
    try:
        metadata, price_grid, rnd_values = await _prepare_rnd_data(
            ticker, expiration, r_annual, q_annual, oi_min, n_grid=400
        )
        pg = np.array(price_grid)
        rnd = np.array(rnd_values)

        mask_above = pg >= price_target
        mask_below = pg < price_target

        p_above = float(np.trapezoid(rnd[mask_above], pg[mask_above])) if mask_above.any() else 0.0
        p_below = float(np.trapezoid(rnd[mask_below], pg[mask_below])) if mask_below.any() else 0.0

        return {
            "ticker": metadata["ticker"],
            "expiration": metadata["expiration"],
            "spot": metadata["spot"],
            "price_target": price_target,
            "tau_days": metadata["tau_days"],
            "p_above": round(p_above, 6),
            "p_below": round(p_below, 6),
            "p_above_pct": round(p_above * 100, 2),
            "p_below_pct": round(p_below * 100, 2),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error: {exc}") from exc
