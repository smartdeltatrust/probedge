from fastapi import APIRouter
from api.models.schemas import OptionsResponse

router = APIRouter(prefix="/options", tags=["options"])


@router.get("/{ticker}", response_model=OptionsResponse)
async def get_options(ticker: str, expiration: str = None):
    """
    Retorna la cadena de opciones y RND para un ticker dado.
    Fase 1: stub — implementación en Fase 2 conectando Massive API.
    """
    return OptionsResponse(
        ticker=ticker.upper(),
        status="not_implemented",
        data={"message": "Fase 2: conectar Massive API", "expiration": expiration},
    )
