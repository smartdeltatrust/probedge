# app.py — ProbEdge Unified App
# Tabs: 📊 Densidades | 🏢 Empresa | 💰 Valuación | 📈 Financieros | 🌐 Sectorial
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

st.set_page_config(
    page_title="ProbEdge — Análisis de Mercados",
    layout="wide",
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
# CACHES — Fundamentales (FMP)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300)
def cached_company_profile(ticker: str, fmp_key: str):
    try:
        from modules.services.company_profile_service import get_company_profile
        return get_company_profile(ticker)
    except Exception as e:
        return None


@st.cache_data(ttl=300)
def cached_key_metrics(ticker: str, fmp_key: str):
    try:
        from modules.services.key_metrics_service import build_key_metrics_grouped_payload
        return build_key_metrics_grouped_payload(symbol=ticker)
    except Exception as e:
        return None


@st.cache_data(ttl=300)
def cached_income_statement(ticker: str, fmp_key: str, period: str = "quarter", limit: int = 12):
    try:
        from modules.services.income_statement_service import get_income_statement_plot_data
        return get_income_statement_plot_data(symbol=ticker, fmp_key=fmp_key, period=period, limit=limit)
    except Exception as e:
        return None


@st.cache_data(ttl=300)
def cached_income_growth(ticker: str, fmp_key: str):
    try:
        from modules.services.income_statement_growth_service import build_income_growth_payload
        return build_income_growth_payload(symbol=ticker, fmp_key=fmp_key, limit=5, period="FY")
    except Exception as e:
        return None


@st.cache_data(ttl=300)
def cached_sector_peers(ticker: str, fmp_key: str, peers_limit: int = 20):
    try:
        from modules.services.sector_peers_analysis_service import build_sector_peers_panel
        return build_sector_peers_panel(symbol=ticker, peers_limit=peers_limit, fmp_api_key=fmp_key)
    except Exception as e:
        return None


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
    st.subheader("Risk-Neutral Density Probabilities from Options Prices")

    if not _check_paywall():
        st.stop()

    chart_container = st.container()
    below_chart_container = st.container()
    controls_container = st.container()

    fmp_api_key = FMP_API_KEY
    if not fmp_api_key:
        st.error("FMP API key is not configured. Set FMP_API_KEY in your .env.")
        st.stop()
    try:
        _get_tt_token()
    except Exception:
        st.error(
            "⚠️ No se pudo conectar con tastytrade. "
            "Verifica las variables TASTYTRADE_LOGIN y TASTYTRADE_PASSWORD en Render."
        )
        st.stop()

    with controls_container:
        col1, col2 = st.columns(2)

        with col1:
            dens_ticker = st.text_input("Ticker (Densidades)", value=ticker, key="dens_ticker").upper().strip()
            range_code = st.selectbox(
                "Historical range",
                options=["d1", "d5", "m1", "m3", "m6", "ytd", "y1", "y2", "y5", "max"],
                index=["d1", "d5", "m1", "m3", "m6", "ytd", "y1", "y2", "y5", "max"].index(
                    settings.DEFAULT_RANGE
                ),
                key="dens_range",
            )

        with col2:
            available_expiries: list[str] = []
            if dens_ticker:
                try:
                    available_expiries = cached_expiries(dens_ticker)
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

                expiry_str = st.selectbox(
                    "Expiry",
                    options=available_expiries,
                    index=best_idx,
                    key="dens_expiry",
                )
            else:
                expiry_str = st.text_input("Expiry (YYYY-MM-DD)", value="2025-12-19", key="dens_expiry_manual")

            col_hist_win, col_fut_win = st.columns(2)
            with col_hist_win:
                past_days = st.number_input("Historical window (days)", min_value=30, max_value=2000, value=120, step=10, key="dens_past")
            with col_fut_win:
                future_days = st.slider("Forward window (days)", min_value=7, max_value=730, value=60, step=7, key="dens_future")

            r_rate = st.number_input("Risk-free rate (r, annual)", value=float(settings.DEFAULT_RATE), step=0.005, format="%.3f", key="dens_r")
            q_rate = st.number_input("Dividend yield (q, annual)", value=0.0, step=0.005, format="%.3f", key="dens_q")

    hist_sigma_rel = float(settings.HIST_SIGMA_REL)
    st.caption("Data: tastytrade (options · real-time) · FMP (historical OHLCV)")

    try:
        quotes_df = cached_quotes(dens_ticker, range_code, fmp_api_key)
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
        spot_q = get_quotes_from_env([dens_ticker])
        if spot_q.get(dens_ticker, {}).get("price"):
            spot = float(spot_q[dens_ticker]["price"])
        else:
            spot = float(quotes_df.loc[quotes_df["Date"] == valuation_date, "Close"].iloc[0])
    except Exception:
        spot = float(quotes_df.loc[quotes_df["Date"] == valuation_date, "Close"].iloc[0])

    try:
        expiry_date = pd.to_datetime(expiry_str)
    except Exception:
        st.error("Invalid expiry date format.")
        st.stop()

    try:
        options_df = cached_options(dens_ticker, expiry_str)
    except RuntimeError as e:
        st.error(f"Could not download options chain: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        st.stop()

    if options_df is None or options_df.empty:
        st.warning(f"No options data for **{dens_ticker}** expiry **{expiry_str}**. Try a different expiry.")
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

    with below_chart_container:
        show_heatmap = st.checkbox("Show density heatmap", value=False, key="chk_density_heatmap")

    with chart_container:
        plot_main_figure(
            quotes_df, dates_win, price_grid, density_win,
            expiry_dates=expiry_dates_win, valuation_date=valuation_date,
            show_heatmap=show_heatmap,
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

    st.markdown(r"""
### Mathematical summary of the methodology

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
# TAB: EMPRESA
# ─────────────────────────────────────────────
def render_empresa(ticker: str):
    st.subheader(f"🏢 Perfil de Empresa — {ticker}")

    if not FMP_API_KEY:
        st.error("FMP_API_KEY no configurada.")
        return

    with st.spinner(f"Cargando perfil de {ticker}..."):
        profile = cached_company_profile(ticker, FMP_API_KEY)

    if profile is None:
        st.error(f"No se pudo obtener perfil para **{ticker}**. Verifica el ticker o la conexión a FMP.")
        return

    # Mostrar logo si disponible
    logo = getattr(profile, "logo_url", None) or getattr(profile, "image_url", None)
    name = getattr(profile, "name", None) or ticker
    sector = getattr(profile, "sector", None) or "N/D"
    industry = getattr(profile, "industry", None) or "N/D"
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
        st.caption(f"**Sector:** {sector} · **Industria:** {industry}")
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

    # Descripción en español via Claude (streaming)
    st.markdown("#### Descripción del negocio")

    if not description_en:
        st.info("No hay descripción disponible para este ticker en FMP.")
        return

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        st.warning("⚠️ ANTHROPIC_API_KEY no configurada — mostrando descripción en inglés.")
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
        st.warning(f"Error en análisis Claude: {e}")
        st.markdown(description_en[:800])


# ─────────────────────────────────────────────
# TAB: VALUACIÓN
# ─────────────────────────────────────────────
def render_valuacion(ticker: str):
    st.subheader(f"💰 Valuación por Múltiplos — {ticker}")

    if not FMP_API_KEY:
        st.error("FMP_API_KEY no configurada.")
        return

    with st.spinner(f"Cargando métricas de {ticker}..."):
        payload = cached_key_metrics(ticker, FMP_API_KEY)

    if payload is None:
        st.error(f"No se pudieron obtener métricas para **{ticker}**.")
        return

    group_a = payload.groups.get("A. Valoración y múltiplos", {})
    group_b = payload.groups.get("B. Rentabilidad y retornos", {})

    def fmt_big(v):
        if v is None:
            return "N/D"
        try:
            x = float(v)
            if abs(x) >= 1e12:
                return f"{x/1e12:.2f}T"
            if abs(x) >= 1e9:
                return f"{x/1e9:.2f}B"
            if abs(x) >= 1e6:
                return f"{x/1e6:.2f}M"
            return f"{x:,.2f}"
        except Exception:
            return str(v)

    def fmt_ratio(v):
        if v is None:
            return "N/D"
        try:
            return f"{float(v):,.2f}x"
        except Exception:
            return str(v)

    def fmt_pct(v):
        if v is None:
            return "N/D"
        try:
            x = float(v)
            x_scaled = x * 100 if abs(x) <= 1.5 else x
            return f"{x_scaled:.2f}%"
        except Exception:
            return str(v)

    st.markdown("##### Múltiplos clave (TTM)")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Market Cap", fmt_big(group_a.get("marketCap")))
    with c2:
        st.metric("EV", fmt_big(group_a.get("enterpriseValueTTM")))
    with c3:
        st.metric("EV/EBITDA", fmt_ratio(group_a.get("evToEBITDATTM")))
    with c4:
        st.metric("ROIC", fmt_pct(group_b.get("returnOnInvestedCapitalTTM")))

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        st.metric("EV/Ventas", fmt_ratio(group_a.get("evToSalesTTM")))
    with c6:
        st.metric("EV/FCF", fmt_ratio(group_a.get("evToFreeCashFlowTTM")))
    with c7:
        st.metric("Núm. Graham", fmt_big(group_a.get("grahamNumberTTM")))
    with c8:
        st.metric("Graham Net-Net", fmt_big(group_a.get("grahamNetNetTTM")))

    # Rentabilidad
    st.markdown("##### Rentabilidad y retornos (TTM)")
    ret_cols = st.columns(4)
    ret_keys = [
        ("ROA", "returnOnAssetsTTM"),
        ("ROE", "returnOnEquityTTM"),
        ("ROIC", "returnOnInvestedCapitalTTM"),
        ("FCF Yield", "freeCashFlowYieldTTM"),
    ]
    for i, (label, key) in enumerate(ret_keys):
        with ret_cols[i]:
            st.metric(label, fmt_pct(group_b.get(key)))

    st.divider()

    # Análisis Claude streaming
    st.markdown("#### Dictamen CFA — Valuación")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        st.warning("⚠️ ANTHROPIC_API_KEY no configurada — análisis Claude no disponible.")
        return

    try:
        from modules.llm_anthropic import stream_valuation_from_multiples
        placeholder = st.empty()
        buffer = ""
        for chunk in stream_valuation_from_multiples(
            symbol=ticker,
            multiples=group_a,
            model=ANTHROPIC_MODEL,
        ):
            buffer += chunk
            placeholder.markdown(buffer + "▌")
        placeholder.markdown(buffer)
    except Exception as e:
        st.warning(f"Error en análisis Claude: {e}")


# ─────────────────────────────────────────────
# TAB: FINANCIEROS
# ─────────────────────────────────────────────
def render_financieros(ticker: str):
    st.subheader(f"📈 Estado de Resultados — {ticker}")

    if not FMP_API_KEY:
        st.error("FMP_API_KEY no configurada.")
        return

    col_period, col_limit = st.columns(2)
    with col_period:
        period = st.selectbox("Período", ["quarter", "annual"], key="fin_period")
    with col_limit:
        limit = st.slider("Períodos a mostrar", 4, 20, 12, key="fin_limit")

    with st.spinner(f"Cargando estados financieros de {ticker}..."):
        plot_data = cached_income_statement(ticker, FMP_API_KEY, period=period, limit=limit)

    if plot_data is None or plot_data.df.empty:
        st.error(f"No se pudieron obtener estados financieros para **{ticker}**.")
        return

    df = plot_data.df.copy()
    div = plot_data.scale_div
    label = plot_data.scale_label

    # Gráfica de barras agrupadas
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        colors = {
            "revenue": "#2196F3",
            "grossProfit": "#4CAF50",
            "ebit": "#FF9800",
            "ebitda": "#9C27B0",
            "operatingIncome": "#F44336",
            "netIncome": "#009688",
        }
        metric_labels = {
            "revenue": "Revenue",
            "grossProfit": "Gross Profit",
            "ebit": "EBIT",
            "ebitda": "EBITDA",
            "operatingIncome": "Op. Income",
            "netIncome": "Net Income",
        }

        x_labels = df["date"].dt.strftime("%Y-%m") if "date" in df.columns else df.index.astype(str)

        for metric in plot_data.available_metrics:
            if metric in df.columns:
                fig.add_trace(go.Bar(
                    x=x_labels,
                    y=df[metric] / div,
                    name=metric_labels.get(metric, metric),
                    marker_color=colors.get(metric, "#888"),
                ))

        fig.update_layout(
            barmode="group",
            title=f"{ticker} — Income Statement ({label})",
            xaxis_title="Período",
            yaxis_title=label,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            height=420,
            margin=dict(l=40, r=20, t=60, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.warning("plotly no disponible — mostrando tabla.")
        display_cols = ["date"] + [m for m in plot_data.available_metrics if m in df.columns]
        df_display = df[display_cols].copy()
        for m in plot_data.available_metrics:
            if m in df_display.columns:
                df_display[m] = (df_display[m] / div).round(2)
        st.dataframe(df_display, use_container_width=True)

    st.divider()

    # Análisis de crecimiento Claude
    st.markdown("#### Análisis de Crecimiento (Claude)")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        st.warning("⚠️ ANTHROPIC_API_KEY no configurada.")
        return

    with st.spinner("Cargando datos de crecimiento..."):
        growth_payload = cached_income_growth(ticker, FMP_API_KEY)

    if growth_payload is None:
        st.warning("No se pudieron obtener datos de crecimiento.")
        return

    try:
        from modules.llm_anthropic import stream_income_growth_analysis
        placeholder = st.empty()
        buffer = ""
        for chunk in stream_income_growth_analysis(
            symbol=ticker,
            groups_latest=growth_payload.groups_latest,
            trend=growth_payload.trend,
            model=ANTHROPIC_MODEL,
        ):
            buffer += chunk
            placeholder.markdown(buffer + "▌")
        placeholder.markdown(buffer)
    except Exception as e:
        st.warning(f"Error en análisis Claude: {e}")


# ─────────────────────────────────────────────
# TAB: SECTORIAL
# ─────────────────────────────────────────────
def render_sectorial(ticker: str):
    st.subheader(f"🌐 Análisis Sectorial — {ticker}")

    if not FMP_API_KEY:
        st.error("FMP_API_KEY no configurada.")
        return

    peers_limit = st.slider("Número de peers", 10, 40, 20, key="sec_peers")

    with st.spinner(f"Buscando peers sectoriales para {ticker} (puede tardar ~30s)..."):
        panel = cached_sector_peers(ticker, FMP_API_KEY, peers_limit=peers_limit)

    if panel is None:
        st.error(f"No se pudo construir el panel sectorial para **{ticker}**.")
        return

    sector = panel.sector or "N/D"
    industry = panel.industry or "N/D"
    company_name = panel.company_name or ticker

    st.caption(f"**{company_name}** · Sector: {sector} · Industria: {industry} · Peers evaluados: {len(panel.peers_table)}")

    # Value-Quality ranking
    if not panel.value_quality_rank.empty:
        st.markdown("##### 🏆 Ranking Value-Quality (score mayor = mejor)")
        cols_vq = ["symbol", "companyName", "evToEBITDATTM", "roic", "score"]
        vq_display = panel.value_quality_rank[[c for c in cols_vq if c in panel.value_quality_rank.columns]].head(15).copy()
        for num_col in ["evToEBITDATTM", "roic", "score"]:
            if num_col in vq_display.columns:
                vq_display[num_col] = vq_display[num_col].round(3)
        st.dataframe(vq_display, use_container_width=True, hide_index=True)
    else:
        st.info("No hay datos suficientes para construir ranking Value-Quality.")

    # ROIC ranking
    if not panel.roic_rank.empty:
        st.markdown("##### 📊 Ranking ROIC")
        cols_roic = ["symbol", "companyName", "returnOnInvestedCapitalTTM", "evToEBITDATTM"]
        roic_display = panel.roic_rank[[c for c in cols_roic if c in panel.roic_rank.columns]].head(15).copy()
        for num_col in ["returnOnInvestedCapitalTTM", "evToEBITDATTM"]:
            if num_col in roic_display.columns:
                roic_display[num_col] = roic_display[num_col].round(3)
        st.dataframe(roic_display, use_container_width=True, hide_index=True)

    # Stats ROIC
    if panel.roic_stats:
        st.markdown("##### Estadísticos ROIC del set")
        sc1, sc2, sc3, sc4 = st.columns(4)
        stats = panel.roic_stats
        with sc1:
            st.metric("Mediana ROIC", f"{stats.get('roic_median', 0)*100:.1f}%")
        with sc2:
            st.metric("P75 ROIC", f"{stats.get('roic_p75', 0)*100:.1f}%")
        with sc3:
            st.metric("Min ROIC", f"{stats.get('roic_min', 0)*100:.1f}%")
        with sc4:
            st.metric("Max ROIC", f"{stats.get('roic_max', 0)*100:.1f}%")

    st.divider()

    # Dictamen sectorial Claude
    st.markdown("#### Dictamen Sectorial (Claude)")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        st.warning("⚠️ ANTHROPIC_API_KEY no configurada — dictamen Claude no disponible.")
        return

    try:
        from modules.llm_anthropic import stream_sector_peers_dictamen

        # Preparar CSV compactos para el LLM
        vq_csv = ""
        roic_csv = ""
        stats_text = ""

        if not panel.value_quality_rank.empty:
            cols_llm = ["symbol", "evToEBITDATTM", "roic", "score"]
            vq_sub = panel.value_quality_rank[[c for c in cols_llm if c in panel.value_quality_rank.columns]].head(10)
            vq_csv = vq_sub.round(3).to_csv(index=False)

        if not panel.roic_rank.empty:
            cols_llm2 = ["symbol", "returnOnInvestedCapitalTTM", "evToEBITDATTM"]
            roic_sub = panel.roic_rank[[c for c in cols_llm2 if c in panel.roic_rank.columns]].head(10)
            roic_csv = roic_sub.round(3).to_csv(index=False)

        if panel.roic_stats:
            s = panel.roic_stats
            stats_text = (
                f"median={s.get('roic_median',0)*100:.1f}%, "
                f"p75={s.get('roic_p75',0)*100:.1f}%, "
                f"min={s.get('roic_min',0)*100:.1f}%, "
                f"max={s.get('roic_max',0)*100:.1f}%, "
                f"n={int(s.get('n_ranked', 0))}"
            )

        placeholder = st.empty()
        buffer = ""
        for chunk in stream_sector_peers_dictamen(
            symbol=ticker,
            sector=sector,
            industry=industry,
            peers_limit=peers_limit,
            value_quality_table_csv=vq_csv,
            roic_table_csv=roic_csv,
            stats_text=stats_text,
            model=ANTHROPIC_MODEL,
        ):
            buffer += chunk
            placeholder.markdown(buffer + "▌")
        placeholder.markdown(buffer)
    except Exception as e:
        st.warning(f"Error en dictamen Claude: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    # Sidebar: ticker global (para todos los tabs de fundamentales)
    with st.sidebar:
        st.markdown("## ProbEdge")
        global_ticker = st.text_input(
            "Ticker (fundamentales)",
            value="MSFT",
            key="global_ticker",
            help="Ticker usado en tabs Empresa, Valuación, Financieros y Sectorial",
        ).upper().strip()

        st.caption("El tab Densidades tiene su propio selector de ticker.")
        st.divider()
        st.caption("Data: FMP · tastytrade · Anthropic Claude")

    # Tabs
    tab_dens, tab_emp, tab_val, tab_fin, tab_sec = st.tabs(
        ["📊 Densidades", "🏢 Empresa", "💰 Valuación", "📈 Financieros", "🌐 Sectorial"]
    )

    with tab_dens:
        try:
            render_densidades(global_ticker)
        except Exception as e:
            st.error(f"Error en tab Densidades: {e}")
            import traceback
            st.code(traceback.format_exc())

    with tab_emp:
        try:
            render_empresa(global_ticker)
        except Exception as e:
            st.error(f"Error en tab Empresa: {e}")

    with tab_val:
        try:
            render_valuacion(global_ticker)
        except Exception as e:
            st.error(f"Error en tab Valuación: {e}")

    with tab_fin:
        try:
            render_financieros(global_ticker)
        except Exception as e:
            st.error(f"Error en tab Financieros: {e}")

    with tab_sec:
        try:
            render_sectorial(global_ticker)
        except Exception as e:
            st.error(f"Error en tab Sectorial: {e}")


if __name__ == "__main__":
    main()
