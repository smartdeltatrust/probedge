"""
modules/data_provider
=====================
Capa de abstracción de datos para Risk-Neutral-Density-Probabilities.

Proveedores disponibles:
- massive  : opciones (cadena, Greeks, IV, OI, vencimientos) via Massive/Polygon.io
- fmp      : OHLC histórico de stocks via Financial Modeling Prep
"""
from __future__ import annotations

from .massive import (
    fetch_options_chain,
    fetch_available_expiries,
    fetch_options_snapshot,
)
from .fmp import fetch_quote_history

__all__ = [
    "fetch_options_chain",
    "fetch_available_expiries",
    "fetch_options_snapshot",
    "fetch_quote_history",
]
