# app.py — ProbEdge Unified App
# Tabs: 📊 Densities | 🏢 Company | 💰 Valuation | 📈 Financials | 🌐 Sector
from __future__ import annotations

import sys
import os
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

from assets.config.settings import settings
from modules.data_provider.tastytrade_options import (
    fetch_available_expiries,
    fetch_options_snapshot,
    get_spot_price as tt_spot_price,
    _get_tt_token,
)
from modules.data_provider.dxfeed_quotes import get_quotes_from_env
from modules.data_provider.fmp import fetch_quote_history as fmp_quote_history
from modules.utils import (
    compute_rnd_from_calls,
    compute_rnd_from_clean_calls,
    build_time_price_density,
    build_clean_calls_from_chain,
)
from modules.plots import plot_main_figure

# Asegurar raíz del proyecto en sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Favicon SVG path. Resolved relative to app.py so it works both locally and
# on Render regardless of CWD. Streamlit accepts SVG paths since v1.30+.
ICON_PATH = ROOT / "assets" / "icon.svg"

st.set_page_config(
    page_title="ProbEdge — Markets Analytics",
    page_icon=str(ICON_PATH) if ICON_PATH.exists() else "📊",
    layout="wide",
)

# Tipografía moderna estilo fintech (Inter para UI, JetBrains Mono para datos).
# Inter = sans geométrica que usan tastytrade/Robinhood/Linear; JetBrains Mono =
# monospace con alternates matemáticos (cero cortado, ligatures), mucho más
# moderna que Consolas pero con el mismo espíritu técnico/financiero.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');

    html, body, [class*="css"], .stApp, .stMarkdown, .stTextInput, .stSelectbox,
    .stNumberInput, .stCheckbox, .stRadio, .stButton, .stTextArea,
    .stCaption, .stAlert, h1, h2, h3, h4, h5, h6, p, label, span, div {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        font-feature-settings: "cv11", "ss01", "ss03";
    }
    code, pre, .stCode, .stDataFrame, .stDataFrameGlideDataEditor,
    .stMetric [data-testid="stMetricValue"], .stMetric [data-testid="stMetricLabel"] {
        font-family: 'JetBrains Mono', 'Consolas', 'SF Mono', monospace !important;
        font-feature-settings: "calt", "zero", "ss01";
    }
    /* Captions de Streamlit con tracking ligero para look financiero */
    .stCaption, [data-testid="stCaptionContainer"] {
        letter-spacing: 0.01em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# === ENTORNO ===
APP_ENV = os.getenv("APP_ENV", "").strip().lower()
IS_DEV = APP_ENV in ("", "dev", "development")

# === API KEYS ===
FMP_API_KEY = os.getenv("FMP_API_KEY", "") or settings.FMP_API_KEY or ""
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929").strip()

# En Stripe Dashboard creas un Payment Link para tu suscripción
STRIPE_PAYMENT_LINK = "https://buy.stripe.com/eVq3cx1Isbd74qLd6BcfK01"


# ─────────────────────────────────────────────
# CACHES — Densidades
# ─────────────────────────────────────────────
@st.cache_data
def cached_quotes(ticker: str, range_code: str, fmp_api_key: str):
    range_to_days = {
        "d1": 1, "d5": 5, "m1": 21, "m3": 63, "m6": 126,
        "ytd": 252, "y1": 252, "y2": 504, "y5": 1260, "max": 0,
    }
    days = range_to_days.get(range_code, 252)
    return fmp_quote_history(ticker, fmp_api_key, days=days)


@st.cache_data(ttl=300)
def cached_expiries(ticker: str):
    tt_token = _get_tt_token()
    return fetch_available_expiries(ticker, tt_token)


@st.cache_data(ttl=60)
def cached_options(ticker: str, expiry: str):
    tt_token = _get_tt_token()
    df = fetch_options_snapshot(ticker, expiry, tt_token)
    if df.empty:
        return df
    df = df.rename(columns={
        "contract_type": "option_type",
        "last_price": "last_close",
    })
    bid = df["bid"].astype(float)
    ask = df["ask"].astype(float)
    last = df["last_close"].astype(float) if "last_close" in df.columns else pd.Series(np.nan, index=df.index)
    mid = np.where(
        (bid > 0) & (ask > 0),
        0.5 * (bid + ask),
        np.where(bid > 0, bid, np.where(ask > 0, ask, np.where(last > 0, last, np.nan))),
    )
    df["mid_price"] = mid
    df["price"] = df["mid_price"]
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# PoP table — premium-selling reference (heatmap-styled DataFrame)
# ─────────────────────────────────────────────
def _build_pop_table(K_grid, pdf_K, spot):
    """
    Tabla de referencia para venta de prima. Para una grilla de strikes
    sampleados a niveles fijos del CDF, computa Call PoP y Put PoP risk-neutral.

    Convención:
      - Call PoP = P(S_T ≤ K) = CDF(K)        → prob de que un short call expire OTM.
      - Put  PoP = P(S_T ≥ K) = 1 − CDF(K)    → prob de que un short put expire OTM.

    El CDF se extrae de la RND al vencimiento (ya incorpora todo el skew de IV).
    """
    K_grid = np.asarray(K_grid, dtype=float)
    pdf_K = np.asarray(pdf_K, dtype=float)
    if len(K_grid) < 2:
        return None
    dx_K = float(K_grid[1] - K_grid[0])
    pdf_clean = np.clip(np.nan_to_num(pdf_K), 0, None)
    if pdf_clean.sum() <= 0 or dx_K <= 0:
        return None
    cdf = np.cumsum(pdf_clean) * dx_K
    if cdf[-1] <= 0:
        return None
    cdf = cdf / cdf[-1]

    # Niveles del CDF a los que sampleamos el strike (orden ascendente).
    levels = [0.05, 0.10, 0.16, 0.25, 0.35, 0.50, 0.65, 0.75, 0.84, 0.90, 0.95]
    rows = []
    for lvl in levels:
        idx = int(np.searchsorted(cdf, lvl))
        idx = max(0, min(idx, len(K_grid) - 1))
        K = float(K_grid[idx])
        call_pop = lvl * 100.0
        put_pop = (1.0 - lvl) * 100.0
        pct_spot = (K - float(spot)) / float(spot) * 100.0 if spot else 0.0
        rows.append({
            "Call PoP": round(call_pop, 1),
            "Strike": round(K, 2),
            "Δ spot": round(pct_spot, 1),
            "Put PoP": round(put_pop, 1),
        })
    return pd.DataFrame(rows)


def _render_pop_table(df: "pd.DataFrame"):
    """
    Renderiza la tabla con heatmap por celda — colormap custom verde/gris/rojo
    matching el lenguaje visual del cono (tastytrade).
    """
    from matplotlib.colors import LinearSegmentedColormap
    pop_cmap = LinearSegmentedColormap.from_list(
        "ttrade_pop",
        ["#ff3366", "#3a3a3a", "#00d4aa"],
    )
    styled = (
        df.style
        .background_gradient(cmap=pop_cmap, subset=["Call PoP"], vmin=0, vmax=100)
        .background_gradient(cmap=pop_cmap, subset=["Put PoP"], vmin=0, vmax=100)
        .format({
            "Call PoP": "{:.0f}%",
            "Strike": "USD {:,.2f}",
            "Δ spot": "{:+.1f}%",
            "Put PoP": "{:.0f}%",
        })
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────
# Skew interpretation (Anthropic API)
# ─────────────────────────────────────────────
def _compute_skew_payload(K_grid, pdf_K, ticker, spot, expiry_date, dte):
    """
    Extrae stats clave de la RND al vencimiento para pasárselos al LLM.
    Devuelve dict listo para serializar a JSON, o None si la densidad es degenerada.
    """
    K_grid = np.asarray(K_grid, dtype=float)
    pdf_K = np.asarray(pdf_K, dtype=float)
    if len(K_grid) < 2:
        return None
    dx_K = float(K_grid[1] - K_grid[0])
    pdf_clean = np.clip(np.nan_to_num(pdf_K), 0, None)
    if pdf_clean.sum() <= 0 or dx_K <= 0:
        return None

    cdf_K = np.cumsum(pdf_clean) * dx_K
    if cdf_K[-1] <= 0:
        return None
    cdf_K = cdf_K / cdf_K[-1]

    def _q(level):
        idx = int(np.searchsorted(cdf_K, level))
        idx = max(0, min(idx, len(K_grid) - 1))
        return float(K_grid[idx])

    quantiles = {
        "q2p5": _q(0.025),
        "q16":  _q(0.16),
        "q50":  _q(0.50),
        "q84":  _q(0.84),
        "q97p5": _q(0.975),
    }

    if (quantiles["q97p5"] - quantiles["q2p5"]) > 0:
        skew = (
            (quantiles["q97p5"] - quantiles["q50"])
            - (quantiles["q50"] - quantiles["q2p5"])
        ) / (quantiles["q97p5"] - quantiles["q2p5"])
    else:
        skew = 0.0

    # Top dense strikes con PoP — misma lógica que los callouts del chart
    y_range = float(np.nanmax(K_grid) - np.nanmin(K_grid))
    min_spacing = max(y_range / 18.0, 1e-9)
    cone_buffer = max(y_range / 35.0, 1e-9)
    cone_edges = list(quantiles.values())

    sorted_idx = np.argsort(pdf_clean)[::-1]
    selected = []
    for idx in sorted_idx:
        if pdf_clean[idx] <= 0:
            break
        K = float(K_grid[idx])
        if any(abs(K - float(K_grid[s])) < min_spacing for s in selected):
            continue
        if any(abs(K - e) < cone_buffer for e in cone_edges):
            continue
        selected.append(idx)
        if len(selected) >= 5:
            break

    dense = []
    for idx in selected:
        K = float(K_grid[idx])
        pop = max(float(cdf_K[idx]), 1.0 - float(cdf_K[idx])) * 100.0
        dense.append({"strike": round(K, 2), "pop_pct": round(pop, 1)})

    return {
        "ticker": ticker,
        "spot": round(float(spot), 2),
        "expiry_date": str(pd.Timestamp(expiry_date).date()),
        "dte": int(dte),
        **{k: round(v, 2) for k, v in quantiles.items()},
        "skew": round(float(skew), 3),
        "dense_strikes": dense,
    }


def _stream_skew_interpretation(payload_json: str, model: str):
    """
    Generator que yieldea deltas de texto desde Anthropic streaming API.
    Permite efecto typewriter en la UI cuando se renderiza con st.empty().
    """
    import json as _json
    import os as _os
    from modules.llm_anthropic import get_anthropic_client, Anthropic as _Anthropic
    if _Anthropic is None:
        yield ("⚠️ 'anthropic' package not installed in this environment. "
               "Add `anthropic>=0.40` to requirements.txt and redeploy.")
        return
    if not (_os.getenv("ANTHROPIC_API_KEY") or "").strip():
        yield "⚠️ ANTHROPIC_API_KEY not set in this environment."
        return
    client = get_anthropic_client()
    if client is None:
        yield "⚠️ Anthropic client unavailable (unknown cause)."
        return
    p = _json.loads(payload_json)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROMPT EDITABLE — modificá libremente para ajustar tono / foco / largo.
    # Las reglas de output (sin $, sin markdown, un párrafo) son críticas para
    # que Streamlit no interprete el texto como LaTeX/Markdown — no las quites.
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    prompt = f"""You analyze options-market data for a premium-selling trader (someone who sells short puts, short calls, or short premium spreads to collect credit on rich implied volatility). The following snapshot was extracted from the {p['ticker']} option chain via Breeden-Litzenberger (risk-neutral density at the {p['dte']}-DTE expiry on {p['expiry_date']}).

Spot price: USD {p['spot']}
Median expected price (50/50): USD {p['q50']}
68 percent confidence range: USD {p['q16']} to USD {p['q84']}
95 percent confidence range: USD {p['q2p5']} to USD {p['q97p5']}
Quantile-based skew score (in [-1, +1], negative means downside-heavy which makes put premium relatively expensive; positive means upside-heavy which makes call premium relatively expensive): {p['skew']:+}
Top density-concentration strikes with risk-neutral PoP (Probability of Profit for an OTM short option at that strike): {p['dense_strikes']}

Write the analysis as EXACTLY TWO short paragraphs separated by ONE blank line. Each paragraph must be 2 to 4 sentences.

Paragraph 1 — premium-selling overview:
- The direction and magnitude of the volatility skew, and therefore which side of the chain (puts or calls) carries the richer, fatter premium right now.
- From the dense strikes list, pick the SINGLE most attractive strike for selling premium given its PoP and its position relative to the median. Name the strike explicitly and its PoP, and label it as a short-put candidate (if below median) or a short-call candidate (if above median).

Paragraph 2 — short-put deep dive (ALWAYS include this paragraph regardless of skew direction):
- The best concrete level for selling a cash-secured short put on this expiry. Pick a strike from the dense strikes that sits below the median; if none qualifies, fall back to a level near the 16 percent or 2.5 percent quantile of the confidence range. Name the strike and its PoP.
- Why this strike makes sense for a put seller (cushion below spot, density support, PoP).
- The tail risks: estimate the percent drop from spot that would breach the short strike, and describe one or two extreme-move scenarios that would put the position in trouble (e.g., a sharp sell-off, an earnings shock, a macro event).

STRICT OUTPUT RULES — follow exactly:
- Plain text only. No headings, no bullet points, no numbered lists, no asterisks, no underscores, no backticks, no tables, no emojis, no markdown formatting of any kind.
- Do NOT use the dollar sign character at all. Write prices as 'USD 510.20' or '510.20 dollars'.
- Output EXACTLY two paragraphs separated by ONE blank line. No other line breaks within paragraphs.
- Do not label the paragraphs ("Paragraph 1", "Paragraph 2", etc.). Just write them flowing.
- Do not start with the ticker name or with a heading. Start directly with the analysis.
- Avoid jargon a retail trader cannot immediately grasp."""
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    try:
        with client.messages.stream(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = getattr(event.delta, "text", "")
                    if delta:
                        yield delta
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception as e:
        yield f"⚠️ Anthropic API error: {e}"


def _render_skew_box(text: str) -> str:
    """
    Envuelve el texto en un div con estilo cyan tenue (Bloomberg/tastytrade)
    y escapa caracteres Markdown sensibles ($ * _ `) para evitar que Streamlit
    interprete el texto como LaTeX/Markdown. Preserva separación de párrafos
    convirtiendo \\n\\n en <br><br>.
    """
    safe = (
        (text or "")
        .replace("\\", "\\\\")
        .replace("$", "\\$")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
    )
    # Separación de párrafos → <br><br>; newlines simples → espacio.
    safe = safe.replace("\r\n", "\n")
    safe = safe.replace("\n\n", "<br><br>")
    safe = safe.replace("\n", " ")
    return (
        "<div style=\""
        "background-color: rgba(0, 180, 220, 0.04);"
        "border-left: 3px solid rgba(0, 180, 220, 0.35);"
        "border-radius: 4px;"
        "padding: 14px 18px;"
        "margin: 8px 0;"
        "color: #cccccc;"
        "font-family: 'Inter', -apple-system, sans-serif;"
        "font-size: 13.5px;"
        "line-height: 1.7;"
        "letter-spacing: 0.005em;"
        f"\">{safe}</div>"
    )


# ─────────────────────────────────────────────
# CACHES — Fundamentales (FMP)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300)
def cached_company_profile(ticker: str, fmp_key: str):
    try:
        from modules.services.company_profile_service import get_company_profile
        return get_company_profile(ticker)
    except Exception as e:
        return None


# NOTE: cached_key_metrics, cached_income_statement, cached_income_growth,
# and cached_sector_peers were removed in the MVP cleanup. They powered the
# Valuation, Financials, and Sector tabs which are no longer part of the
# app. The underlying modules.services.* code is still on disk in case we
# want to bring those analyses back later.


# ─────────────────────────────────────────────
# PAYWALL (solo producción)
# ─────────────────────────────────────────────
def _check_paywall():
    def es_usuario_pro(api_key: str) -> bool:
        if not api_key:
            return False
        return api_key.strip().upper().startswith("PRO-")

    if IS_DEV:
        return True

    with st.container():
        st.markdown("##### Pro access")
        col_key, col_cta = st.columns([2, 1])
        with col_key:
            pro_key = st.text_input("Pro API key", type="password")
        with col_cta:
            st.markdown("Don't have a key?")
            st.markdown(f"[➡ Get Pro access]({STRIPE_PAYMENT_LINK})", unsafe_allow_html=True)

        if not es_usuario_pro(pro_key):
            st.info("Enter a valid Pro key (starts with 'PRO-') to unlock.")
            return False
    return True


# ─────────────────────────────────────────────
# TAB: DENSIDADES
# ─────────────────────────────────────────────
def render_densidades(ticker: str):
    # Hero banner rendered later (just above the chart) replaces the old
    # st.subheader title — same message, branded layout with the icon.
    if not _check_paywall():
        st.stop()

    fmp_api_key = FMP_API_KEY
    if not fmp_api_key:
        st.error("FMP API key is not configured. Set FMP_API_KEY in your .env.")
        st.stop()
    try:
        _get_tt_token()
    except Exception as _tt_err:
        import os as _os
        def _ck(k: str) -> str:
            return "✅" if _os.environ.get(k) else "❌"
        st.error(
            "⚠️ Could not connect to tastytrade.\n\n"
            f"OAuth Personal Grant: "
            f"CLIENT_ID {_ck('TASTYTRADE_CLIENT_ID')} | "
            f"CLIENT_SECRET {_ck('TASTYTRADE_CLIENT_SECRET')} | "
            f"REFRESH_TOKEN {_ck('TASTYTRADE_REFRESH_TOKEN')}\n\n"
            f"If all three show ✅, the grant was likely revoked at "
            f"my.tastytrade.com → Manage → API Access. Generate a new one and "
            f"update the secret in Render.\n\n"
            f"Error: {_tt_err}"
        )
        st.stop()

    with st.sidebar:
        st.divider()
        st.caption("Densities")

        range_code = st.selectbox(
            "Historical range",
            options=["d1", "d5", "m1", "m3", "m6", "ytd", "y1", "y2", "y5", "max"],
            index=["d1", "d5", "m1", "m3", "m6", "ytd", "y1", "y2", "y5", "max"].index(
                settings.DEFAULT_RANGE
            ),
            key="dens_range",
        )

        available_expiries: list[str] = []
        if ticker:
            try:
                available_expiries = cached_expiries(ticker)
            except RuntimeError as e:
                st.warning(str(e))
                available_expiries = []

        if available_expiries:
            today = pd.Timestamp.today().normalize()
            expiry_dates = []
            for s in available_expiries:
                try:
                    expiry_dates.append(pd.to_datetime(s))
                except Exception:
                    expiry_dates.append(None)

            best_idx = None
            best_distance = None
            for idx, dt in enumerate(expiry_dates):
                if dt is None:
                    continue
                days = (dt - today).days
                if days < 21 or days > 60:
                    continue
                distance = abs(days - 30)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_idx = idx

            if best_idx is None:
                for idx, dt in enumerate(expiry_dates):
                    if dt is None:
                        continue
                    if (dt - today).days >= 7:
                        best_idx = idx
                        break

            if best_idx is None:
                best_idx = len(available_expiries) - 1

            dte_by_expiry: dict[str, int] = {}
            for s, dt in zip(available_expiries, expiry_dates):
                if dt is not None:
                    dte_by_expiry[s] = (dt - today).days

            def _fmt_expiry(s: str) -> str:
                days = dte_by_expiry.get(s)
                if days is None:
                    return s
                if days < 0:
                    return f"{s}  ·  expired"
                if days == 0:
                    return f"{s}  ·  0 DTE (today)"
                return f"{s}  ·  {days} DTE"

            expiry_str = st.selectbox(
                "Expiry",
                options=available_expiries,
                index=best_idx,
                key="dens_expiry",
                format_func=_fmt_expiry,
            )
        else:
            expiry_str = st.text_input("Expiry (YYYY-MM-DD)", value="2025-12-19", key="dens_expiry_manual")

        past_days = st.number_input("Historical window (days)", min_value=30, max_value=2000, value=120, step=10, key="dens_past")

        r_rate = st.number_input("Risk-free rate (r, annual)", value=float(settings.DEFAULT_RATE), step=0.005, format="%.3f", key="dens_r")
        # Dividend yield (q) eliminado del UI — se asume 0 por simplicidad.
        q_rate = 0.0

    # Density heatmap toggle now lives centered BELOW the chart — see the
    # `st.toggle` block after plot_main_figure. We read its current value here
    # so the chart renders with the right overlay on every rerun.
    if "chk_density_heatmap" not in st.session_state:
        st.session_state["chk_density_heatmap"] = False
    show_heatmap = st.session_state["chk_density_heatmap"]

    hist_sigma_rel = float(settings.HIST_SIGMA_REL)
    st.caption("Data: tastytrade (options · real-time) · FMP (historical OHLCV)")

    try:
        quotes_df = cached_quotes(ticker, range_code, fmp_api_key)
    except RuntimeError as e:
        st.error(f"Could not download historical data from FMP: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        st.stop()

    if quotes_df.empty:
        st.error("No historical data for that ticker / range.")
        st.stop()

    valuation_date = quotes_df["Date"].max()
    try:
        spot_q = get_quotes_from_env([ticker])
        if spot_q.get(ticker, {}).get("price"):
            spot = float(spot_q[ticker]["price"])
        else:
            spot = float(quotes_df.loc[quotes_df["Date"] == valuation_date, "Close"].iloc[0])
    except Exception:
        spot = float(quotes_df.loc[quotes_df["Date"] == valuation_date, "Close"].iloc[0])

    try:
        expiry_date = pd.to_datetime(expiry_str)
    except Exception:
        st.error("Invalid expiry date format.")
        st.stop()

    # Forward window = DTE (días hasta expiración). El cono termina exactamente
    # en la fecha de vencimiento elegida; mínimo 7 días para evitar ventanas degeneradas.
    future_days = max(7, int((expiry_date - valuation_date).days))

    try:
        options_df = cached_options(ticker, expiry_str)
    except RuntimeError as e:
        st.error(f"Could not download options chain: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        st.stop()

    if options_df is None or options_df.empty:
        st.warning(f"No options data for **{ticker}** expiry **{expiry_str}**. Try a different expiry.")
        st.stop()

    try:
        clean_calls_df = build_clean_calls_from_chain(
            options_df, S0=spot, valuation_date=valuation_date,
            expiry_date=expiry_date, r_annual=r_rate, q_annual=q_rate,
        )
        if not clean_calls_df.empty:
            K_grid, pdf_K = compute_rnd_from_clean_calls(
                clean_calls_df, spot=spot, valuation_date=valuation_date,
                expiry_date=expiry_date, r_annual=r_rate, q_annual=q_rate,
            )
        else:
            K_grid, pdf_K = compute_rnd_from_calls(
                options_df, spot=spot, valuation_date=valuation_date,
                expiry_date=expiry_date, r_annual=r_rate, q_annual=q_rate,
            )
    except Exception as e:
        st.error(f"Could not build RND: {e}")
        st.stop()

    rnd_by_date = {pd.Timestamp(expiry_date): (K_grid, pdf_K)}

    dates_all, price_grid, density = build_time_price_density(
        quotes_df, rnd_by_date, hist_sigma_rel=hist_sigma_rel, interpolate_future=True,
    )

    min_date = valuation_date - pd.Timedelta(days=int(past_days))
    max_date = valuation_date + pd.Timedelta(days=int(future_days))
    mask = (dates_all >= np.datetime64(min_date)) & (dates_all <= np.datetime64(max_date))

    if mask.sum() == 0:
        st.warning("Selected window does not overlap available data.")
        st.stop()

    dates_win = dates_all[mask]
    density_win = density[:, mask]
    expiry_dates_win = [
        d for d in rnd_by_date.keys()
        if pd.Timestamp(min_date) <= pd.Timestamp(d) <= pd.Timestamp(max_date)
    ]

    # ─── Hero banner ───────────────────────────────────────────────────────
    # Renders inline above the chart: the brand icon (cone of probability)
    # on the left and the wordmark + tagline on the right. The icon SVG is
    # embedded literally (not via st.image) so that strokes/gradients render
    # at full quality without raster downsampling, and so the visual loads
    # in the same DOM pass as the rest of the page (no flicker).
    #
    # IMPORTANT: we strip the XML declaration and any pre-SVG comments from
    # the file before injecting into st.markdown. The browser's HTML parser
    # treats `<?xml ... ?>` as a "bogus comment" and bleeds the surrounding
    # text into the visible DOM — that produced the broken sidebar columns
    # in the first attempt. Pulling just the `<svg>...</svg>` element is the
    # safe form.
    # Build the icon as a base64-encoded data URI inside an <img> tag.
    # Inline <svg> embedding inside Streamlit's markdown container was
    # producing collapsed-to-zero icons because of flex-sizing dependency
    # loops between the SVG's viewBox and its parent's intrinsic width.
    # A data URI <img> sidesteps all of that — the browser treats it as a
    # replaced element with fixed dimensions, exactly like a PNG would
    # behave. Reliable across every browser and Streamlit version.
    import base64 as _base64
    import re as _re
    try:
        _raw_svg = ICON_PATH.read_text(encoding="utf-8")
        _svg_match = _re.search(r"<svg\b.*?</svg>", _raw_svg, _re.DOTALL | _re.IGNORECASE)
        _svg_only = _svg_match.group(0) if _svg_match else ""
        _icon_data_uri = (
            "data:image/svg+xml;base64,"
            + _base64.b64encode(_svg_only.encode("utf-8")).decode("ascii")
        ) if _svg_only else ""
    except Exception:
        _icon_data_uri = ""
    if _icon_data_uri:
        st.markdown(
            f"""
            <div style="
                display: flex;
                align-items: center;
                gap: 22px;
                padding: 18px 24px;
                margin: 4px 0 18px 0;
                border-radius: 10px;
                background: linear-gradient(180deg, rgba(0,180,220,0.04) 0%, rgba(0,0,0,0.0) 100%);
                border-left: 2px solid rgba(0, 180, 220, 0.35);
            ">
                <img src="{_icon_data_uri}" width="72" height="72" alt="ProbEdge"
                     style="flex: 0 0 auto; display: block;"/>
                <div style="flex: 1 1 auto; min-width: 0;">
                    <div style="
                        font-family: 'Inter', -apple-system, sans-serif;
                        font-size: 28px;
                        font-weight: 600;
                        letter-spacing: -0.01em;
                        line-height: 1.05;
                        color: #f0fbff;
                        margin: 0 0 6px 0;
                    ">ProbEdge</div>
                    <div style="
                        font-family: 'Inter', -apple-system, sans-serif;
                        font-size: 13px;
                        font-weight: 400;
                        letter-spacing: 0.01em;
                        color: #8aa9b3;
                        margin: 0;
                    ">Risk-Neutral Density · Live from the option chain</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    plot_main_figure(
        quotes_df, dates_win, price_grid, density_win,
        expiry_dates=expiry_dates_win, valuation_date=valuation_date,
        show_heatmap=show_heatmap,
    )

    # ─── Density heatmap toggle — centered, elegant, prominent ───
    # The heatmap is the headline feature of the visualization (it's what
    # makes the implied-density story visible), so the on/off switch lives
    # right under the chart in its own bordered frame.
    #
    # Sizing strategy: we keep a moderately wide middle column so the toggle
    # label never wraps, BUT we inject CSS that forces the bordered container
    # to `width: fit-content` — that way the border hugs the toggle exactly,
    # independent of viewport width / sidebar state. There is only one
    # bordered container in the whole app, so a global selector is safe.
    st.markdown(
        """
        <style>
        [data-testid="stVerticalBlockBorderWrapper"] {
            width: fit-content !important;
            max-width: 100% !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _spacer_l, _toggle_col, _spacer_r = st.columns([1, 2, 1])
    with _toggle_col:
        with st.container(border=True):
            st.toggle(
                "Show density heatmap",
                key="chk_density_heatmap",
                help="Overlay the implied probability density as a color heatmap. "
                     "Brighter colors = more probability mass concentrated at that price level on that date.",
            )

    # ─── Skew interpretation via Claude (streaming typewriter) ───
    skew_payload = _compute_skew_payload(
        K_grid, pdf_K, ticker, spot, expiry_date, future_days,
    )
    if skew_payload is not None:
        st.divider()
        st.subheader("Skew interpretation")

        import json as _json
        import hashlib as _hashlib

        payload_str = _json.dumps(skew_payload, sort_keys=True)
        payload_hash = _hashlib.md5(payload_str.encode()).hexdigest()
        cache = st.session_state.setdefault("_skew_cache", {})
        placeholder = st.empty()

        if payload_hash in cache:
            # Ya se streameó este payload en la sesión — render estático.
            placeholder.markdown(
                _render_skew_box(cache[payload_hash]),
                unsafe_allow_html=True,
            )
        else:
            # Primera vez para este payload — typewriter desde Anthropic.
            chunks: list[str] = []
            for chunk in _stream_skew_interpretation(payload_str, ANTHROPIC_MODEL):
                chunks.append(chunk)
                placeholder.markdown(
                    _render_skew_box("".join(chunks)),
                    unsafe_allow_html=True,
                )
            cache[payload_hash] = "".join(chunks)

        st.caption(
            "† This interpretation is AI-generated commentary on a risk-neutral "
            "probability study derived from option-chain prices. It is not "
            "financial advice nor a recommendation to trade — use at your own risk."
        )

    # ─── PoP table (premium-selling reference) ───
    df_pop = _build_pop_table(K_grid, pdf_K, spot)
    if df_pop is not None:
        st.divider()
        st.subheader("PoP table — premium-selling reference")
        _render_pop_table(df_pop)
        st.caption(
            "Each row pairs a strike (sampled at fixed CDF levels of the RND at "
            "expiry) with the risk-neutral PoP of a short call (left) and a "
            "short put (right). Greener cells mean a higher probability the "
            "option expires OTM, i.e. the short keeps the full premium."
        )

    # ─── Explanation & Math (unchanged) ───
    st.subheader("Explanation")
    st.markdown(r"""
This chart shows how the options market assigns probabilities to different price levels over time.
The heatmap translates those probabilities into colors so you can see where probability mass is concentrated.

Each point in the time–price plane in the future has an associated density: if the color is very faint,
the market sees that scenario as unlikely; if the color is more intense, many price paths compatible with
current option prices pass through that region.

Historical candles show the prices that actually occurred in the past, while the cone and heatmap show
which combinations of date and price are consistent with option prices under the risk–neutral measure.

You can think of an entire swarm of possible future price paths: the heatmap highlights in brighter colors
the zones where more trajectories accumulate, according to option prices, and leaves almost black the zones
where almost no simulated path arrives.

For example, if 60 days from now the brightest area is near a price of 420, this means that, under the market's
risk–neutral view, it is more likely to find the price around 420 than far above or below that value, and the
68% and 95% bands indicate ranges where most of that probability is concentrated.

Working with implied densities instead of a single "target price" lets you evaluate tail risk, asymmetries
and extreme scenarios, which makes this visualization especially useful to design strategies, size positions,
and understand how the market is pricing future uncertainty.
""")

    # Methodology section is visually separated from the prose explanation:
    # collapsed by default so readers who only want the visual picture aren't
    # buried in formulas, but one click reveals the full Breeden–Litzenberger
    # derivation for the quantitatively inclined.
    with st.expander("Mathematical summary of the methodology", expanded=False):
        st.markdown(r"""
We start from the option chain and build *clean* call prices.
If $C(K)$ is the call price and $P(K)$ the put price at the same strike $K$,
with spot $S_0$, risk–free rate $r$ and dividend yield $q$, we use:
""")
        st.latex(r"""
C_{\text{clean}}(K) \approx
\begin{cases}
\dfrac{\text{bid} + \text{ask}}{2} & \text{if there is a valid spread} \\
P(K) + S_0 e^{-qT} - K e^{-rT} & \text{(put–call parity)} \\
\end{cases}
""")
        st.markdown(r"Then we remove discounting:")
        st.latex(r"""
\tilde C(K) = C_{\text{clean}}(K)\, e^{rT}
\approx
\mathbb{E}_Q\big[(S_T - K)^+\big],
""")
        st.markdown(r"and we apply the Breeden–Litzenberger formula:")
        st.latex(r"f_Q(K) = \frac{\partial^2 \tilde C(K)}{\partial K^2}.")
        st.markdown(r"Numerically, we interpolate, force $f_Q(K) \ge 0$ and normalize:")
        st.latex(r"\int f_Q(K)\, dK = 1,")
        st.markdown(r"also adjusting the first moment to match the theoretical forward:")
        st.latex(r"\mathbb{E}_Q[S_T] = \int K\, f_Q(K)\, dK \approx S_0 e^{(r - q)T}.")
        st.markdown(r"On each historical date $t$ we model intraday uncertainty as a Gaussian centered at the close $S_t$:")
        st.latex(r"""
p_{\text{hist}}(s \mid t)
\propto
\exp\left(
-\frac{1}{2}\,
\frac{(s - S_t)^2}{(\sigma_{\text{hist}} S_t)^2}
\right),
""")
        st.markdown(r"with fixed $\sigma_{\text{hist}}$ relative to the price. The quantile $q_\alpha(t)$ satisfies:")
        st.latex(r"\int_{-\infty}^{q_\alpha(t)} p_t(s)\, ds = \alpha,")
        st.markdown(r"and from these we obtain the 68% and 95% confidence bands that define the probability cone shown in the chart.")


# ─────────────────────────────────────────────
# TAB: COMPANY
# ─────────────────────────────────────────────
def render_empresa(ticker: str):
    st.subheader(f"🏢 Company Profile — {ticker}")

    if not FMP_API_KEY:
        st.error("FMP_API_KEY is not set.")
        return

    with st.spinner(f"Loading profile for {ticker}..."):
        profile = cached_company_profile(ticker, FMP_API_KEY)

    if profile is None:
        st.error(f"Could not fetch profile for **{ticker}**. Check the ticker or FMP connectivity.")
        return

    # Mostrar logo si disponible
    logo = getattr(profile, "logo_url", None) or getattr(profile, "image_url", None)
    name = getattr(profile, "name", None) or ticker
    sector = getattr(profile, "sector", None) or "N/A"
    industry = getattr(profile, "industry", None) or "N/A"
    website = getattr(profile, "website", None) or ""
    description_en = getattr(profile, "description_en", None) or ""

    col_logo, col_info = st.columns([1, 4])
    with col_logo:
        if logo:
            try:
                st.image(logo, width=80)
            except Exception:
                pass
    with col_info:
        st.markdown(f"### {name} (`{ticker}`)")
        st.caption(f"**Sector:** {sector} · **Industry:** {industry}")
        if website:
            st.caption(f"[{website}]({website})")

    # Facts básicos (market cap, etc.)
    facts = getattr(profile, "facts", None)
    if facts:
        cols = st.columns(4)
        items = list(facts.items())
        for i, (k, v) in enumerate(items[:8]):
            with cols[i % 4]:
                st.metric(k, v)

    st.divider()

    # Business description (LLM streaming summary)
    st.markdown("#### Business Description")

    if not description_en:
        st.info("No description available for this ticker on FMP.")
        return

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        st.warning("⚠️ ANTHROPIC_API_KEY not set — showing raw FMP description.")
        st.markdown(description_en[:800] + ("..." if len(description_en) > 800 else ""))
        return

    try:
        from modules.llm_anthropic import stream_translate_and_summarize
        desc_placeholder = st.empty()
        buffer = ""
        for chunk in stream_translate_and_summarize(
            english_text=description_en,
            sector=sector,
            model=ANTHROPIC_MODEL,
            max_words=120,
        ):
            buffer += chunk
            desc_placeholder.markdown(buffer + "▌")
        desc_placeholder.markdown(buffer)
    except Exception as e:
        st.warning(f"Claude analysis error: {e}")
        st.markdown(description_en[:800])


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    # Single-page MVP layout:
    #   sidebar → header (ProbEdge wordmark) + Ticker input + company snapshot
    #   main flow → render_densidades (hero banner + chart + heatmap toggle +
    #                                   skew interpretation + methodology expander)
    #             → divider
    #             → render_empresa (company logo + facts + AI description)
    #             → sidebar footer (data attribution)
    #
    # Tabs (Valuation / Financials / Sector) were removed during MVP cleanup
    # — only Densities and Company remain, fused into one continuous scroll.
    with st.sidebar:
        st.markdown("## ProbEdge")
        global_ticker = st.text_input(
            "Ticker",
            value="SPY",
            key="global_ticker",
            help="Ticker driving the chart and the company snapshot.",
        ).upper().strip()

        # Company name + short profile (FMP). Cached → only one call per ticker.
        # Renders silently if FMP key or profile is unavailable, so the sidebar
        # never breaks for unsupported tickers (e.g. some indices/futures).
        if FMP_API_KEY and global_ticker:
            try:
                _sidebar_profile = cached_company_profile(global_ticker, FMP_API_KEY)
            except Exception:
                _sidebar_profile = None
            if _sidebar_profile is not None:
                _name = getattr(_sidebar_profile, "name", None) or global_ticker
                _sector = getattr(_sidebar_profile, "sector", None) or ""
                _industry = getattr(_sidebar_profile, "industry", None) or ""
                _desc = getattr(_sidebar_profile, "description_en", None) or ""

                st.markdown(f"**{_name}**")
                if _sector or _industry:
                    sector_line = " · ".join([x for x in [_sector, _industry] if x])
                    st.caption(sector_line)
                if _desc:
                    # Trim to ~240 chars and end at last full sentence/word.
                    _MAX = 240
                    if len(_desc) > _MAX:
                        _cut = _desc[:_MAX]
                        # backtrack to last sentence end or whitespace for clean break
                        _stop = max(_cut.rfind(". "), _cut.rfind("? "), _cut.rfind("! "))
                        if _stop < 120:
                            _stop = _cut.rfind(" ")
                        _desc_short = (_cut[:_stop + 1] if _stop > 0 else _cut) + "…"
                    else:
                        _desc_short = _desc
                    st.caption(_desc_short)

    # ── Densities section (hero banner + chart + heatmap toggle + skew + math) ──
    try:
        render_densidades(global_ticker)
    except Exception as e:
        st.error(f"Error rendering Densities: {e}")
        import traceback
        st.code(traceback.format_exc())

    # ── Company section (logo + facts + AI description), inlined below chart ──
    st.divider()
    try:
        render_empresa(global_ticker)
    except Exception as e:
        st.error(f"Error rendering Company section: {e}")

    # Pie del sidebar (al final, después de toda la página principal).
    with st.sidebar:
        st.divider()
        st.caption("Data: FMP · tastytrade · Anthropic Claude")


if __name__ == "__main__":
    main()
