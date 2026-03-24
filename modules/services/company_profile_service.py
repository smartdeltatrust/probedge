# src/services/company_profile_service.py
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict
import inspect

from modules.data_provider.fmp_fundamentals import get_profile_by_symbol
from modules.domain.models import CompanyProfile


def _fmt_market_cap(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "N/D"
    if v >= 1e12:
        return f"{v/1e12:.2f}T"
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:,.0f}"


def _fmt_float(x: Any, nd: int = 3) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "N/D"


def _fmt_int(x: Any) -> str:
    try:
        return f"{int(float(x)):,}"
    except Exception:
        return "N/D"


def _build_facts_from_fmp(raw: Dict[str, Any]) -> "OrderedDict[str, str]":
    # 52W High/Low desde "range" tipo "164.08-260.1"
    hi, lo = "N/D", "N/D"
    r = (raw.get("range") or "").strip()
    if "-" in r:
        parts = [p.strip() for p in r.split("-", 1)]
        if len(parts) == 2:
            lo = parts[0] or "N/D"
            hi = parts[1] or "N/D"

    facts = OrderedDict()
    facts["Market Cap"] = _fmt_market_cap(raw.get("marketCap"))
    facts["Beta"] = _fmt_float(raw.get("beta"), 3)
    facts["P/E (TTM)"] = _fmt_float(raw.get("pe"), 5)
    facts["EPS (TTM)"] = _fmt_float(raw.get("eps"), 2)
    facts["Forward P/E"] = _fmt_float(raw.get("forwardPE"), 5)
    facts["52W High"] = hi
    facts["52W Low"] = lo
    facts["Industry"] = (raw.get("industry") or "N/D")
    facts["Sector"] = (raw.get("sector") or "N/D")
    facts["CEO"] = (raw.get("ceo") or "N/D")
    facts["fullTimeEmployees"] = _fmt_int(raw.get("fullTimeEmployees"))
    return facts


def _company_profile_allowed_kwargs() -> set[str]:
    """
    Detecta qué kwargs soporta CompanyProfile para evitar errores tipo:
    'unexpected keyword argument ...'
    """
    try:
        sig = inspect.signature(CompanyProfile)
        return {k for k in sig.parameters.keys() if k != "self"}
    except Exception:
        # Fallback conservador
        return {"ticker"}


def get_company_profile(symbol: str) -> CompanyProfile:
    s = (symbol or "").upper().strip()
    raw = get_profile_by_symbol(s)

    if not raw:
        # Crear el mínimo viable sin romper el constructor
        allowed = _company_profile_allowed_kwargs()
        payload_min = {"ticker": s, "name": s}
        payload_min = {k: v for k, v in payload_min.items() if k in allowed}
        return CompanyProfile(**payload_min)

    # Payload "rico" (lo que quisiéramos tener)
    facts = _build_facts_from_fmp(raw)

    payload = {
        "ticker": (raw.get("symbol") or s).upper().strip(),
        "name": (raw.get("companyName") or "").strip(),
        "sector": (raw.get("sector") or "").strip(),
        "industry": (raw.get("industry") or "").strip(),
        "country": (raw.get("country") or "").strip(),
        "website": (raw.get("website") or "").strip(),

        # IMPORTANTE: tu UI usa description_en, no description
        "description_en": (raw.get("description") or "").strip(),

        # Extras útiles
        "ceo": (raw.get("ceo") or "").strip(),
        "full_time_employees": raw.get("fullTimeEmployees"),

        # Logo (FMP). Si tu modelo usa otro nombre (image_url), lo cubrimos también.
        "logo_url": (raw.get("image") or "").strip(),
        "image_url": (raw.get("image") or "").strip(),

        # Facts ya formateados
        "facts": facts,

        # Campos numéricos por si luego quieres KPIs/series
        "market_cap": raw.get("marketCap"),
        "beta": raw.get("beta"),
        "pe_ttm": raw.get("pe"),
        "eps_ttm": raw.get("eps"),
        "forward_pe": raw.get("forwardPE"),
        "range_52w": raw.get("range"),
        "exchange": (raw.get("exchange") or "").strip(),
    }

    # Filtramos solo lo que CompanyProfile realmente acepta
    allowed = _company_profile_allowed_kwargs()
    filtered = {k: v for k, v in payload.items() if k in allowed}

    # Si por alguna razón name no existe en el modelo, al menos ticker queda
    if "ticker" not in filtered:
        filtered["ticker"] = s

    return CompanyProfile(**filtered)
