"""
api/routes/options.py
Endpoints de opciones — conectado a Massive API.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import sys, os, math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from modules.data_provider.massive import fetch_available_expiries, fetch_options_snapshot
from api.core.config import get_settings

router = APIRouter(prefix="/options", tags=["options"])

def clean_record(row: dict) -> dict:
    """Reemplaza NaN/Inf con None para serialización JSON limpia."""
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in row.items()
    }

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
    expiration: Optional[str] = Query(None, description="Fecha YYYY-MM-DD. Si no se pasa, usa el primer vencimiento disponible."),
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
