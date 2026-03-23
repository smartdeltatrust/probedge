import os
from dotenv import load_dotenv
from dataclasses import dataclass, field

load_dotenv()

# Intentar leer de Streamlit secrets si estamos en Streamlit Cloud
def _get(key: str, default: str = "") -> str:
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return val
    except Exception:
        pass
    return os.getenv(key, default)


@dataclass
class Settings:
    FINVIZ_BASE_URL: str = "https://elite.finviz.com"
    FINVIZ_AUTH_TOKEN: str = field(default_factory=lambda: _get("FINVIZ_AUTH_TOKEN"))
    MASSIVE_API_KEY: str = field(default_factory=lambda: _get("MASSIVE_API_KEY"))
    FMP_API_KEY: str = field(default_factory=lambda: _get("FMP_API_KEY"))

    DEFAULT_RANGE: str = "ytd"
    DEFAULT_RATE: float = 0.04
    HIST_SIGMA_REL: float = 0.01
    N_STRIKE_POINTS: int = 200
    N_PRICE_POINTS: int = 300
    PRICE_PADDING: float = 0.10


settings = Settings()
