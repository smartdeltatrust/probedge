"""
api/models/schemas.py
Schemas Pydantic para todas las respuestas de la API.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any


# --- Health ---
class HealthResponse(BaseModel):
    status: str
    version: str
    message: str


# --- Options: Expiries ---
class ExpiriesResponse(BaseModel):
    ticker: str
    expiries: List[str]
    count: int


# --- Options: Chain ---
class OptionContract(BaseModel):
    strike: float
    contract_type: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last_price: Optional[float] = None
    open_interest: Optional[float] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    volume: Optional[float] = None


class ChainResponse(BaseModel):
    ticker: str
    expiration: str
    count: int
    returned: int
    columns: List[str]
    data: List[dict]  # flexible — columnas varían por provider


# --- Options: RND ---
class RNDResponse(BaseModel):
    ticker: str
    expiration: str
    spot: float
    tau_days: int
    r_annual: float
    n_grid: int
    price_grid: List[Optional[float]]
    rnd: List[Optional[float]]


# --- Options: Probabilities ---
class ProbabilitiesResponse(BaseModel):
    ticker: str
    expiration: str
    spot: float
    price_target: float
    tau_days: int
    p_above: float = Field(..., description="P(S_T > price_target)")
    p_below: float = Field(..., description="P(S_T < price_target)")
    p_above_pct: float = Field(..., description="P(S_T > price_target) en %")
    p_below_pct: float = Field(..., description="P(S_T < price_target) en %")


# --- Market: Quote ---
class QuoteResponse(BaseModel):
    ticker: str
    name: Optional[str] = None
    price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[int] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    market_cap: Optional[int] = None
    prev_close: Optional[float] = None
    open: Optional[float] = None


# --- Market: History ---
class OHLCVBar(BaseModel):
    Date: str
    Open: float
    High: float
    Low: float
    Close: float
    Volume: int


class HistoryResponse(BaseModel):
    ticker: str
    days: int
    data: List[dict]  # flexible por compatibilidad con FMP


# --- Legacy stubs (para no romper imports viejos) ---
class OptionsResponse(BaseModel):
    ticker: str
    status: str
    data: Any = None


class MarketResponse(BaseModel):
    ticker: str
    status: str
    data: Any = None
