from fastapi import APIRouter
from api.models.schemas import MarketResponse

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/{ticker}", response_model=MarketResponse)
async def get_market_data(ticker: str):
    """
    Retorna datos OHLC y de mercado para un ticker dado.
    Fase 1: stub — implementación en Fase 2 conectando FMP API.
    """
    return MarketResponse(
        ticker=ticker.upper(),
        status="not_implemented",
        data={"message": "Fase 2: conectar FMP API"},
    )
