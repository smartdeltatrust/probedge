import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def compute_quantile_bands(price_grid: np.ndarray, density: np.ndarray):
    """
    A partir de la matriz density [n_precios x n_tiempos], calcula
    bandas 68% (16-84) y 95% (2.5-97.5) y la mediana (50%) para cada columna.
    Devuelve:
      q2p5, q16, q50, q84, q97p5  (cada uno array len = n_tiempos)
    """
    n_p, n_t = density.shape
    if n_p < 2 or n_t < 1:
        return None, None, None, None, None

    dx = price_grid[1] - price_grid[0]
    q2p5 = np.full(n_t, np.nan)
    q16 = np.full(n_t, np.nan)
    q50 = np.full(n_t, np.nan)
    q84 = np.full(n_t, np.nan)
    q97p5 = np.full(n_t, np.nan)

    for j in range(n_t):
        pdf = density[:, j]
        if not np.isfinite(pdf).any():
            continue
        if pdf.sum() <= 0:
            continue

        pdf = np.clip(pdf, 0, None)
        cdf = np.cumsum(pdf) * dx
        if cdf[-1] <= 0:
            continue
        cdf /= cdf[-1]

        def q_level(level: float):
            idx = np.searchsorted(cdf, level)
            idx = min(max(idx, 0), len(price_grid) - 1)
            return price_grid[idx]

        q2p5[j] = q_level(0.025)
        q16[j] = q_level(0.16)
        q50[j] = q_level(0.50)
        q84[j] = q_level(0.84)
        q97p5[j] = q_level(0.975)

    return q2p5, q16, q50, q84, q97p5

def plot_main_figure(
    quotes_df: pd.DataFrame,
    dates_all: np.ndarray,
    price_grid: np.ndarray,
    density: np.ndarray,
    expiry_dates: list[pd.Timestamp],
    valuation_date: pd.Timestamp,
    show_heatmap: bool = True,
    show_past_rnd: bool = False,
):
    """
    Figura principal: OHLC + cono RND (68/95%) + mediana +
    opcionalmente heatmap y bandas RND históricas.
    Bloomberg/tastytrade ultra-dark theme.
    """

    valuation_date = pd.Timestamp(valuation_date)

    # Eje X y máscaras pasado/futuro
    x_vals = pd.to_datetime(dates_all)
    y_vals = price_grid
    mask_future = x_vals >= valuation_date
    mask_past = ~mask_future

    # Limitamos velas a la ventana visible
    mask_hist_win = (quotes_df["Date"] >= x_vals.min()) & (quotes_df["Date"] <= x_vals.max())
    quotes_win = quotes_df.loc[mask_hist_win].copy()
    if quotes_win.empty:
        quotes_win = quotes_df.copy()

    # Bandas de confianza (todo el grid)
    q2p5, q16, q50, q84, q97p5 = compute_quantile_bands(price_grid, density)

    fig = go.Figure()

    # -------------------------------------------------
    # 1) Heatmap diferencial del skew (solo si show_heatmap = True)
    #
    # Densidad normalizada por columna → preserva la FORMA del slice en todo
    # el horizonte (sin esto, la dispersión ~√T hace que los tails se vean
    # siempre oscuros y se pierde el skew). Luego split por la mediana:
    # verde arriba (upside mass), rojo abajo (downside mass). El asimetría de
    # los plumajes ES el skew de IV pricing-in por el mercado de opciones.
    # -------------------------------------------------
    if show_heatmap and q50 is not None:
        # Densidad normalizada por columna → preserva la FORMA del slice
        z_norm = density.astype(float).copy()
        col_max = np.nanmax(z_norm, axis=0)
        safe_max = np.where(col_max > 0, col_max, 1.0)
        z_norm = z_norm / safe_max
        z_norm = np.nan_to_num(z_norm, nan=0.0, posinf=0.0, neginf=0.0)

        # Signo respecto a mediana per-column → mapa diverging único
        median_arr = q50
        finite_med = np.isfinite(median_arr)
        above_mask_2d = (price_grid[:, None] >= median_arr[None, :]) & finite_med[None, :]
        sign_matrix = np.where(above_mask_2d, 1.0, -1.0)
        sign_matrix = np.where(finite_med[None, :], sign_matrix, 0.0)
        z_signed = z_norm * sign_matrix

        if mask_past.any():
            z_signed[:, mask_past] = 0.0

        # CDF per-column (vectorizado) → exceedance prob para hover
        dx = float(price_grid[1] - price_grid[0])
        pdf_clean = np.clip(np.nan_to_num(density.astype(float)), 0, None)
        cdf = np.cumsum(pdf_clean, axis=0) * dx
        col_total = cdf[-1, :]
        safe_total = np.where(col_total > 0, col_total, 1.0)
        cdf = cdf / safe_total
        exceedance = np.where(above_mask_2d, 1.0 - cdf, cdf)
        exceedance = np.clip(exceedance, 0.0, 1.0)
        exceedance = np.where(finite_med[None, :], exceedance, 0.0)

        # Hover text per celda: flecha + prob de cola + rango de confianza que
        # contiene la celda. En las zonas más densas (cerca de la mediana), el
        # rango asociado es 68% conf — ahí "se parquea" la confianza alta.
        # Más lejos del centro, el rango cae en 95% conf y, en los tails, fuera.
        inside_68 = (
            (price_grid[:, None] >= q16[None, :])
            & (price_grid[:, None] <= q84[None, :])
        )
        inside_95 = (
            (price_grid[:, None] >= q2p5[None, :])
            & (price_grid[:, None] <= q97p5[None, :])
        )
        custom_text = np.full(z_signed.shape, "—", dtype=object)
        for j in range(z_signed.shape[1]):
            if not finite_med[j]:
                continue
            for i in range(z_signed.shape[0]):
                arrow = "↑" if above_mask_2d[i, j] else "↓"
                if inside_68[i, j]:
                    rng_lbl = "68% conf cone"
                elif inside_95[i, j]:
                    rng_lbl = "95% conf cone"
                else:
                    rng_lbl = "outside 95% (tail)"
                custom_text[i, j] = (
                    f"{arrow} {exceedance[i, j] * 100:.2f}% tail · {rng_lbl}"
                )

        fig.add_trace(go.Heatmap(
            x=x_vals,
            y=y_vals,
            z=z_signed,
            customdata=custom_text,
            colorscale=[
                [0.00, "rgba(255,90,130,0.90)"],
                [0.25, "rgba(220,60,100,0.55)"],
                [0.40, "rgba(160,40,70,0.30)"],
                [0.50, "rgba(0,0,0,0)"],
                [0.60, "rgba(0,140,100,0.30)"],
                [0.75, "rgba(0,200,150,0.55)"],
                [1.00, "rgba(0,255,200,0.90)"],
            ],
            zmin=-1.0,
            zmax=1.0,
            zsmooth="best",
            showscale=False,
            name="RND",
            showlegend=False,
            hovertemplate="$%{y:.2f}  ·  %{customdata}<extra>RND skew</extra>",
        ))

    # -------------------------------------------------
    # 2) Velas OHLC
    # -------------------------------------------------
    fig.add_trace(
        go.Candlestick(
            x=quotes_win["Date"],
            open=quotes_win["Open"],
            high=quotes_win["High"],
            low=quotes_win["Low"],
            close=quotes_win["Close"],
            name="Underlying (OHLC)",
            increasing=dict(
                line=dict(color="#00d4aa", width=1.4),
                fillcolor="rgba(0,212,170,0.40)",
            ),
            decreasing=dict(
                line=dict(color="#ff3366", width=1.4),
                fillcolor="rgba(255,51,102,0.40)",
            ),
            showlegend=True,
        )
    )

    # -------------------------------------------------
    # 3) Bandas RND: SOLO futuro (cono) + mediana
    # -------------------------------------------------
    if q2p5 is not None:
        # --- futuro: bandas rellenas 95% y 68% ---
        if mask_future.any():
            x_future = x_vals[mask_future]
            q2p5_f = q2p5[mask_future]
            q97p5_f = q97p5[mask_future]
            q16_f = q16[mask_future]
            q84_f = q84[mask_future]

            # 95% lower → P(S_T ≤ K) = 2.5%
            trace_95_lower = go.Scatter(
                x=x_future,
                y=q2p5_f,
                mode="lines",
                line=dict(color="#1e50b4", width=1),
                showlegend=False,
                hovertemplate="95%↓ $%{y:.2f}<extra></extra>",
            )
            # 95% upper → cota superior del rango 95% conf
            trace_95_upper = go.Scatter(
                x=x_future,
                y=q97p5_f,
                mode="lines",
                line=dict(color="#1e50b4", width=1),
                fill="tonexty",
                fillcolor="rgba(30, 80, 180, 0.15)",
                name="95% conf cone",
                hovertemplate="95%↑ $%{y:.2f}<extra></extra>",
            )

            # 68% lower → cota inferior del rango 68% conf
            trace_68_lower = go.Scatter(
                x=x_future,
                y=q16_f,
                mode="lines",
                line=dict(color="#00b4dc", width=1),
                showlegend=False,
                hovertemplate="68%↓ $%{y:.2f}<extra></extra>",
            )
            # 68% upper → cota superior del rango 68% conf
            trace_68_upper = go.Scatter(
                x=x_future,
                y=q84_f,
                mode="lines",
                line=dict(color="#00b4dc", width=1),
                fill="tonexty",
                fillcolor="rgba(0, 180, 220, 0.25)",
                name="68% conf cone",
                hovertemplate="68%↑ $%{y:.2f}<extra></extra>",
            )

            fig.add_trace(trace_95_lower)
            fig.add_trace(trace_95_upper)
            fig.add_trace(trace_68_lower)
            fig.add_trace(trace_68_upper)

        # Mediana (hist + futuro), tenue para no tapar velas
        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=q50,
                mode="lines",
                line=dict(color="#ffffff", width=1.4),
                opacity=0.5,
                name="Median (50/50)",
                hovertemplate="• Median: $%{y:.2f}<extra></extra>",
            )
        )

        # Rango Y basado en todo el 95%
        all_low = np.nanmin(q2p5[np.isfinite(q2p5)])
        all_high = np.nanmax(q97p5[np.isfinite(q97p5)])
        if np.isfinite(all_low) and np.isfinite(all_high) and all_high > all_low:
            span = all_high - all_low
            y_min = all_low - 0.05 * span
            y_max = all_high + 0.05 * span
            fig.update_yaxes(range=[y_min, y_max])

        # Skew score de Pearson basado en cuantiles, acotado en [-1, +1].
        # Negativo → cola izquierda gorda (downside-heavy, skew típico de equity).
        # Positivo → cola derecha gorda (upside-heavy, raro fuera de biotechs/eventos).
        if mask_future.any():
            j_term = int(np.where(mask_future)[0][-1])
            up_dist = q97p5[j_term] - q50[j_term]
            dn_dist = q50[j_term] - q2p5[j_term]
            if (
                np.isfinite(up_dist)
                and np.isfinite(dn_dist)
                and (up_dist + dn_dist) > 0
            ):
                skew_q = float((up_dist - dn_dist) / (up_dist + dn_dist))
                if skew_q < -0.05:
                    skew_lbl, skew_color = "downside-heavy", "#ff5a82"
                elif skew_q > 0.05:
                    skew_lbl, skew_color = "upside-heavy", "#00ffc8"
                else:
                    skew_lbl, skew_color = "near-symmetric", "#aaaaaa"

                fig.add_annotation(
                    x=0.005,
                    y=0.99,
                    xref="paper",
                    yref="paper",
                    xanchor="left",
                    yanchor="top",
                    text=(
                        f"RND skew @ exp: {skew_q:+.2f}  ·  {skew_lbl}<br>"
                        f"<span style='color:#00ffc8'>▲ upside</span>  "
                        f"<span style='color:#ff5a82'>▼ downside</span> mass"
                    ),
                    showarrow=False,
                    align="left",
                    font=dict(color=skew_color, size=10, family="JetBrains Mono, Consolas, monospace"),
                    bgcolor="rgba(0,0,0,0.65)",
                    bordercolor="#333333",
                    borderwidth=1,
                    borderpad=5,
                )

        # Callouts de bordes de los rangos de confianza al terminal del cono.
        # 95%↑ / 95%↓ = cotas del intervalo del 95% (P(S_T en rango) = 95%)
        # 68%↑ / 68%↓ = cotas del intervalo del 68%
        # El skew de IV ya está incorporado vía Breeden-Litzenberger, así que la
        # asimetría de los precios alrededor de la mediana refleja el skew real.
        if mask_future.any():
            x_term = x_vals[mask_future][-1]
            q_callouts = [
                (q97p5[mask_future][-1], "↑", "95%", "#1e50b4"),
                (q84[mask_future][-1],   "↑", "68%", "#00b4dc"),
                (q50[mask_future][-1],   "•", "med", "#ffffff"),
                (q16[mask_future][-1],   "↓", "68%", "#00b4dc"),
                (q2p5[mask_future][-1],  "↓", "95%", "#1e50b4"),
            ]
            for y_val, arrow, prob_lbl, color in q_callouts:
                if not np.isfinite(y_val):
                    continue
                if arrow == "•":
                    text = f"med ${float(y_val):,.2f}"
                else:
                    text = f"{prob_lbl}{arrow} ${float(y_val):,.2f}"
                fig.add_annotation(
                    x=x_term,
                    y=float(y_val),
                    xref="x",
                    yref="y",
                    xanchor="right",
                    yanchor="middle",
                    xshift=-4,
                    text=text,
                    showarrow=False,
                    font=dict(color=color, size=10, family="JetBrains Mono, Consolas, monospace"),
                    bgcolor="rgba(0,0,0,0.55)",
                    borderpad=2,
                    opacity=0.95,
                )

            # Strikes de máxima densidad al vencimiento + PoP risk-neutral.
            # Los rows brillantes del heatmap suelen alinear con strikes reales del
            # chain (Breeden-Litzenberger concentra masa cerca de strikes con quotes).
            # PoP(K) = max(CDF(K), 1−CDF(K)) — prob risk-neutral de que un OTM short
            # en ese strike expire OTM (interpretación tastytrade-style del delta IV).
            pdf_term = density[:, j_term].astype(float)
            pdf_clean = np.clip(np.nan_to_num(pdf_term), 0, None)
            if pdf_clean.sum() > 0:
                dx_grid = float(price_grid[1] - price_grid[0])
                cdf_term = np.cumsum(pdf_clean) * dx_grid
                if cdf_term[-1] > 0:
                    cdf_term = cdf_term / cdf_term[-1]

                    med_term = q50[j_term]
                    cone_edges = [
                        e for e in (q50[j_term], q16[j_term], q84[j_term],
                                    q2p5[j_term], q97p5[j_term])
                        if np.isfinite(e)
                    ]

                    y_range = float(np.nanmax(price_grid) - np.nanmin(price_grid))
                    min_spacing = max(y_range / 18.0, 1e-9)
                    cone_buffer = max(y_range / 35.0, 1e-9)

                    sorted_idx = np.argsort(pdf_clean)[::-1]
                    selected = []
                    for idx in sorted_idx:
                        if pdf_clean[idx] <= 0:
                            break
                        K = float(price_grid[idx])
                        if any(abs(K - float(price_grid[s])) < min_spacing
                               for s in selected):
                            continue
                        if any(abs(K - e) < cone_buffer for e in cone_edges):
                            continue
                        selected.append(idx)
                        if len(selected) >= 5:
                            break

                    for idx in selected:
                        K = float(price_grid[idx])
                        cdf_K = float(cdf_term[idx])
                        pop = max(cdf_K, 1.0 - cdf_K) * 100.0
                        if np.isfinite(med_term) and K >= med_term:
                            color = "#00ffc8"
                        else:
                            color = "#ff5a82"
                        fig.add_annotation(
                            x=x_term,
                            y=K,
                            xref="x",
                            yref="y",
                            xanchor="right",
                            yanchor="middle",
                            xshift=-4,
                            text=f"${K:,.2f} · PoP {pop:.0f}%",
                            font=dict(color=color, size=9,
                                      family="JetBrains Mono, Consolas, monospace"),
                            showarrow=False,
                            bgcolor="rgba(0,0,0,0.50)",
                            borderpad=2,
                            opacity=0.92,
                        )


    # -------------------------------------------------
    # 4) Líneas verticales de vencimiento
    # -------------------------------------------------
    if len(price_grid) > 0:
        y_min_shapes = float(np.nanmin(price_grid))
        y_max_shapes = float(np.nanmax(price_grid))
    else:
        y_min_shapes, y_max_shapes = 0.0, 1.0

    for d_exp in expiry_dates:
        x_val = pd.Timestamp(d_exp).to_pydatetime()

        fig.add_shape(
            type="line",
            x0=x_val,
            x1=x_val,
            y0=y_min_shapes,
            y1=y_max_shapes,
            xref="x",
            yref="y",
            line=dict(
                color="#ffd700",
                width=1,
                dash="dot",
            ),
        )

        fig.add_annotation(
            x=x_val,
            y=y_max_shapes,
            text=str(pd.Timestamp(d_exp).date()),
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font=dict(color="#ffd700", size=10),
        )

    # -------------------------------------------------
    # 5) Estética general — Bloomberg/tastytrade ultra dark
    # -------------------------------------------------
    fig.update_layout(
        template=None,
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        xaxis_title="Date",
        yaxis_title="Price / Strike",
        title=dict(
            text="Densidad de Probabilidad Implícita a Partir de Precios de Opciones",
            font=dict(family="Inter, -apple-system, sans-serif", size=16, color="#dddddd"),
        ),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#1a1a1a",
            font=dict(color="#ffffff", family="JetBrains Mono, Consolas, monospace"),
            bordercolor="#333333",
        ),
        font=dict(
            family="JetBrains Mono, Consolas, monospace",
            color="#aaaaaa",
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.15,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(0,0,0,0.7)",
            bordercolor="#333333",
            borderwidth=1,
            font=dict(color="#aaaaaa"),
        ),
        margin=dict(l=30, r=70, t=40, b=40),
        height=760,
    )

    # Días hábiles ausentes en el histórico = feriados (NYSE u otros mercados cerrados).
    # Plotly comprime el eje X saltándose fines de semana + esos días, eliminando huecos visuales.
    trading_days = pd.to_datetime(quotes_df["Date"]).dt.normalize().unique()
    holidays_list: list[str] = []
    if len(trading_days) > 0:
        td_index = pd.DatetimeIndex(trading_days)
        full_weekdays = pd.date_range(td_index.min(), td_index.max(), freq="B")
        holidays = full_weekdays.difference(td_index)
        holidays_list = [d.strftime("%Y-%m-%d") for d in holidays]

    fig.update_xaxes(
        showgrid=True,
        gridcolor="#1a1a1a",
        zerolinecolor="#1a1a1a",
        rangeslider_visible=False,
        tickfont=dict(color="#888888"),
        title_font=dict(color="#aaaaaa"),
        rangebreaks=[
            dict(bounds=["sat", "mon"]),
            dict(values=holidays_list),
        ],
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#1a1a1a",
        zerolinecolor="#1a1a1a",
        side="right",
        ticks="inside",
        ticklen=4,
        tickcolor="#333333",
        tickfont=dict(color="#888888"),
        title_font=dict(color="#aaaaaa"),
        title_standoff=12,
    )

    st.plotly_chart(fig, width="stretch")
