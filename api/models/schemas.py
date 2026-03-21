from pydantic import BaseModel
from typing import Optional


class HealthResponse(BaseModel):
    status: str
    version: str
    message: str = "RND Probabilities API running"


class TickerRequest(BaseModel):
    ticker: str
    expiration: Optional[str] = None  # formato: YYYY-MM-DD


class OptionsResponse(BaseModel):
    ticker: str
    status: str
    data: Optional[dict] = None


class MarketResponse(BaseModel):
    ticker: str
    status: str
    data: Optional[dict] = None
