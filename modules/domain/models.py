# src/domain/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

# =========================
# Helpers (dominio)
# =========================
def _parse_date_yyyy_mm_dd(x: Any) -> Optional[date]:
    """
    FMP suele devolver fechas "YYYY-MM-DD" (a veces con tiempo).
    Truncamos a 10 chars y convertimos a date.
    """
    if not x:
        return None
    try:
        s = str(x).strip()
        if len(s) >= 10:
            s = s[:10]
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _to_float_or_none(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None


def _to_int_or_none(x: Any) -> Optional[int]:
    if x is None or x == "":
        return None
    try:
        return int(x)
    except Exception:
        return None


# =========================
# Company Profile (dominio)
# =========================
@dataclass
class CompanyProfile:
    ticker: str

    # Identidad
    name: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    exchange: Optional[str] = None

    # Logo / imagen
    logo_url: Optional[str] = None  # (puede ser Logokit o FMP image)
    image_url: Optional[str] = None  # FMP field: "image"

    # Descripción (fuente primaria FMP)
    description_en: Optional[str] = None  # FMP field: "description"

    # Quick facts (FMP)
    market_cap: Optional[float] = None
    range_52w: Optional[str] = None
    beta: Optional[float] = None
    change_pct: Optional[float] = None
    avg_volume: Optional[float] = None
    full_time_employees: Optional[int] = None


# =========================
# Earnings Report (dominio)
# =========================
@dataclass(frozen=True)
class EarningsReportItem:
    symbol: str
    date: Optional[date] = None
    eps_actual: Optional[float] = None
    eps_estimated: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_estimated: Optional[float] = None
    last_updated: Optional[date] = None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EarningsReportItem":
        return EarningsReportItem(
            symbol=str(d.get("symbol") or "").upper(),
            date=_parse_date_yyyy_mm_dd(d.get("date")),
            eps_actual=_to_float_or_none(d.get("epsActual")),
            eps_estimated=_to_float_or_none(d.get("epsEstimated")),
            revenue_actual=_to_float_or_none(d.get("revenueActual")),
            revenue_estimated=_to_float_or_none(d.get("revenueEstimated")),
            last_updated=_parse_date_yyyy_mm_dd(d.get("lastUpdated")),
        )


def pick_next_earnings_date(items: List[EarningsReportItem], today: Optional[date] = None) -> Optional[date]:
    """
    Selecciona la fecha futura más cercana (>= hoy).
    No uses epsActual como criterio porque puede venir null aunque el evento sea futuro.
    """
    ref = today or date.today()
    future_dates = [it.date for it in items if it.date is not None and it.date >= ref]
    if not future_dates:
        return None
    return min(future_dates)



class RNDRequest(BaseModel):
    ticker: str
    expiry: str  # ISO date
    range_code: str = "y1"
    r_annual: float = 0.05
    q_annual: float = 0.015
    past_days: int = 60
    future_days: int = 60
    use_cache: bool = True


class ConeQuantiles(BaseModel):
    p025: List[float]
    p160: List[float]
    p500: List[float]
    p840: List[float]
    p975: List[float]


class ExpiryStats(BaseModel):
    mean_ST: float
    prob_drop_gt_threshold: float
    prob_up_gt_threshold: float
    threshold_pct: float


class RNDResponse(BaseModel):
    ticker: str
    expiry: str
    valuation_date: str
    spot: float
    dates: List[str]
    price_grid: List[float]
    density: List[List[float]]  # [len(price_grid) x len(dates)]
    cone_quantiles: ConeQuantiles
    expiry_stats: ExpiryStats
    expiry_dates: List[str] = Field(default_factory=list)
# src/adapters/api_key_store.py

import os
from dataclasses import dataclass
from typing import Set


@dataclass(frozen=True)
class APIKeyStore:
    allowed_keys: Set[str]

    @staticmethod
    def from_env(var_name: str = "APP_USER_API_KEYS") -> "APIKeyStore":
        raw = (os.getenv(var_name) or "").strip()
        keys = {k.strip() for k in raw.split(",") if k.strip()}
        return APIKeyStore(allowed_keys=keys)

    def is_valid(self, api_key: str) -> bool:
        if not api_key:
            return False
        return api_key.strip() in self.allowed_keys