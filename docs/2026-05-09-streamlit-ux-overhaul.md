# Streamlit UX overhaul — Densidades tab (2026-05-09)

Resumen integral de los cambios funcionales y visuales aplicados a la
aplicación Streamlit (`app.py`) y al pipeline de plotting (`modules/plots.py`)
durante la sesión del 2026-05-09. Continuación natural de los arreglos de
OAuth documentados en `2026-05-08-oauth-migration.md`.

---

## 1 · Resumen ejecutivo

La aplicación pasó de tener controles dispersos debajo del chart y un cono
con callouts genéricos a un layout tipo Bloomberg/tastytrade con todo el
espacio central dedicado al chart, controles agrupados en el sidebar, y una
nueva capa de interpretación generada por Claude al pie del análisis.

**Highlights:**

- Cono de probabilidad re-etiquetado con la lectura correcta (rangos de
  confianza 68 % / 95 % de confianza, no percentiles cumulativos).
- Heatmap rediseñado: skew explícito (verde upside, rojo downside, normalizado
  por columna), score numérico del skew, hover con prob de exceedance + rango
  de confianza por celda.
- Strikes de máxima densidad anotados con su PoP risk-neutral dentro del cono.
- LLM (Claude) genera dos párrafos al pie: vista general de premium-selling +
  análisis específico de short put con tail risks. Streaming typewriter, caja
  estilo terminal, escape defensivo de Markdown.
- Default ticker SPY. Sidebar consolidado. Slider de forward window eliminado
  (ahora = DTE automático). Dividend yield removido del UI (q = 0).

---

## 2 · Cambios por superficie

### 2.1 · Selector de expiry con DTE (`app.py`)

`format_func` en el `selectbox` para mostrar `2026-06-19  ·  42 DTE` mientras
se preserva el valor `YYYY-MM-DD` para el resto del pipeline.

```python
def _fmt_expiry(s: str) -> str:
    days = dte_by_expiry.get(s)
    if days is None:        return s
    if days < 0:            return f"{s}  ·  expired"
    if days == 0:           return f"{s}  ·  0 DTE (today)"
    return f"{s}  ·  {days} DTE"
```

### 2.2 · Chart: huecos eliminados en la serie temporal

`fig.update_xaxes(rangebreaks=[...])` con dos breaks:

1. `dict(bounds=["sat", "mon"])` — fines de semana, fijo.
2. `dict(values=holidays_list)` — feriados inferidos como días hábiles
   (`pd.date_range(..., freq="B")`) ausentes de `quotes_df["Date"]`. Esto
   captura Thanksgiving, 4 de julio, etc. sin hardcodear calendario.

Tanto las velas históricas como la cinta del cono se compactan sin huecos.

### 2.3 · Eje Y al lado derecho (estilo Bloomberg/tastytrade)

`fig.update_yaxes(side="right", ticks="inside", ticklen=4, tickcolor="#333333")`.
Colorbar del heatmap movido a `x=-0.05` (lado izquierdo) con `thickness=10`
para no chocar con el nuevo eje Y.

### 2.4 · Forward window removido del UI

El slider "Forward window (days)" desapareció. Ahora se computa
automáticamente como `future_days = max(7, (expiry_date - valuation_date).days)`
después de parsear el expiry, lo que hace que el cono termine exactamente en
la fecha de vencimiento elegida.

### 2.5 · Dividend yield removido del UI

`q_rate = 0.0` como constante (era el default que el usuario nunca tocaba).
No cambia el comportamiento downstream pero limpia el formulario.

### 2.6 · Cone callouts con lectura de rango de confianza

Los 5 callouts en el extremo derecho del cono pasaron de mostrar **percentiles
cumulativos / probabilidades de cola** a mostrar **bordes de los rangos de
confianza** (la lectura natural del cono):

| Antes (CDF) | Antes (exceedance) | Ahora (rango de confianza) |
|---|---|---|
| `97.5%  $510` | `↑$510  2.5%` | `95%↑ $510` |
| `84%    $475` | `↑$475  16%`  | `68%↑ $475` |
| `50%    $450` | `med $450`    | `med $450` |
| `16%    $425` | `↓$425  16%`  | `68%↓ $425` |
| `2.5%   $390` | `↓$390  2.5%` | `95%↓ $390` |

Lectura: *"el rango entre `95%↓` y `95%↑` contiene al subyacente con 95 % de
confianza al vencimiento"*. El skew sigue siendo visible en la asimetría de
las distancias `med → 95%↑` vs `med → 95%↓`.

### 2.7 · Heatmap rediseñado con skew explícito

**Antes:** un colorscale azul único (denso → claro), donde la dispersión
natural `~√T` hacía que los tails se vieran siempre oscuros y se perdía la
forma del skew a horizontes largos.

**Ahora:** un único heatmap diverging:

- `z_signed[i, j] = (density[i, j] / col_max[j]) * sign(price[i] − median[j])`
- `zmin=-1, zmax=+1`
- Colorscale verde (upside) → transparente (cero) → rojo (downside)
- **Normalización por columna** preserva la **forma** del slice en todo el
  horizonte (no la magnitud absoluta, que decae con T).
- **Asimetría visible:** si el plumaje rojo se extiende más lejos que el
  verde → cola izquierda gorda → skew típico de equity (negativo).

### 2.8 · Skew score en la esquina del chart

Calculado al vencimiento usando la métrica de Pearson basada en cuantiles:

```
skew = ((q97.5 − q50) − (q50 − q2.5)) / (q97.5 − q2.5)
```

Acotado en `[−1, +1]`. Etiqueta dinámica:

- `< −0.05` → "downside-heavy" (rojo `#ff5a82`)
- `> +0.05` → "upside-heavy" (verde `#00ffc8`)
- entre medias → "near-symmetric" (gris)

Render: anotación HTML en la esquina superior izquierda del chart.

### 2.9 · Density-peak strikes con PoP dentro del cono

Detección de los top-5 strikes de máxima densidad en la columna terminal,
con dos filtros:

- **Min spacing** `~yrange/18` para deduplicar picos anchos.
- **Cone buffer** `~yrange/35` para skipear strikes cerca de los edges del
  cono (que ya tienen sus callouts).

Para cada strike seleccionado:

```
PoP(K) = max(CDF(K), 1 − CDF(K)) × 100
```

Lectura tastytrade-style: probabilidad de que un OTM short en ese strike
expire OTM (i.e., delta-equivalente desde el RND). Color verde si está sobre
la mediana, rojo si debajo. Etiquetas posicionadas dentro del cono al
extremo terminal junto a los callouts del rango de confianza.

### 2.10 · Hover enriquecido en el heatmap

Cada celda lleva en `customdata` un string formateado con:

- Flecha direccional (`↑` / `↓`) según el lado de la mediana.
- Probabilidad de exceedance: `P(S_T ≥ K)` arriba, `P(S_T ≤ K)` abajo.
- Rango de confianza que contiene la celda: `inside 68% cone`,
  `inside 95% cone`, o `outside 95% (tail)`.

Hovertemplate: `$%{y:.2f} · %{customdata}<extra>RND skew</extra>`. Con
`hovermode="x unified"`, al pasar el mouse por una fecha se ven simultáneamente
los valores de las bandas del cono + la celda exacta del heatmap bajo el
cursor.

### 2.11 · Default ticker SPY (no MSFT)

`app.py:889` (sidebar global). El selector de Densidades ya heredaba del
global, así que SPY es el default real ahora.

### 2.12 · Layout: chart al centro, controles al sidebar

**Antes:** sidebar con un solo ticker para fundamentales + caption explicando
que "el tab Densidades tiene su propio selector"; controles del chart abajo;
chart en altura por defecto de Plotly (~450 px).

**Ahora:**

- Sidebar consolidado:
  1. `## ProbEdge` (título)
  2. **Ticker** (uno solo, default SPY) — drives todos los tabs
  3. `Densidades` (subhead)
  4. Historical range, Expiry (con DTE), Historical window, Risk-free rate,
     Show density heatmap toggle
  5. Pie: `Data: FMP · tastytrade · Anthropic Claude`
- Centro del tab Densidades: chart full-width.
- Eliminado el widget separado `dens_ticker` — se unificó con el global.

### 2.13 · Altura por defecto del chart aumentada

`fig.update_layout(height=760)`. Default de Plotly era ~450 px. Las
proporciones de los datos se preservan (los ejes son lineales en X/Y; solo
cambian los pixeles disponibles).

### 2.14 · Interpretación LLM al pie (Claude streaming)

Bloque nuevo después del chart, antes del "Explanation" estático.

**Pipeline:**

1. `_compute_skew_payload(K_grid, pdf_K, ticker, spot, expiry_date, dte)`
   extrae stats clave del RND al vencimiento: spot, q2.5/q16/q50/q84/q97.5,
   skew score, top-5 dense strikes con PoP. Devuelve dict serializable.

2. `_stream_skew_interpretation(payload_json, model)` es un generator que
   yieldea text deltas desde Anthropic streaming API
   (`client.messages.stream(...)`).

3. En `render_densidades`, el call site:
   - Computa hash MD5 del payload.
   - Si el hash está en `st.session_state["_skew_cache"]`, render estático
     desde caché.
   - Si no, abre `st.empty()` placeholder y va re-renderizando el div en cada
     chunk del stream (efecto typewriter ChatGPT).
   - Guarda el texto final en session_state cache para el resto de la sesión.

**Estilo del recuadro (`_render_skew_box`):** `<div>` custom con `bg
rgba(0, 180, 220, 0.04)` (cyan tenue), `border-left rgba(0, 180, 220, 0.35)`
de 3 px, font Consolas, color `#cccccc`. Mucho más sutil que `st.info`.

**Sanitización defensiva:** antes de renderizar, escape de `\\ $ * _ \`` para
evitar que Streamlit interprete el texto como LaTeX/Markdown. Doble linea
(`\n\n`) → `<br><br>` para preservar separación de párrafos.

**Disclaimer:** `st.caption()` con prefijo `†` (footnote tradicional financiero):

> † This interpretation is AI-generated commentary on a risk-neutral
> probability study derived from option-chain prices. It is not financial
> advice nor a recommendation to trade — use at your own risk.

### 2.15 · Prompt de Claude: dos párrafos con análisis de short put

El prompt está marcado con divisores `━━━` en el código y construido
dinámicamente con el dict del payload. Estructura:

**§1 — Premium-selling overview** (2-4 oraciones):

- Dirección y magnitud del skew → cuál lado del chain (puts o calls) carga
  la prima más rica.
- El strike más atractivo de la lista densa para vender prima, con su PoP,
  etiquetado como short-put o short-call según mediana.

**§2 — Short-put deep dive** (siempre presente, 2-4 oraciones):

- Strike concreto para cash-secured put (de la lista densa < mediana, o
  fallback a q16/q2.5).
- Por qué tiene sentido (cushion, densidad de soporte, PoP).
- **Tail risks**: % de caída desde spot necesaria para tocar el strike + uno
  o dos escenarios extremos (sell-off, earnings shock, evento macro).

**Reglas estrictas:**

- Plain text, sin headings, bullets, listas, asteriscos, underscores,
  backticks, tablas, emojis, ni Markdown.
- Sin `$`. Precios como `USD 510.20`.
- Exactamente 2 párrafos separados por una línea en blanco.
- Sin labels de párrafo ("Paragraph 1/2"), solo flowing.
- Sin empezar con el ticker o un heading.

---

## 3 · Operacional

### 3.1 · Variables de entorno (Render)

Sin cambios respecto al estado pre-overhaul. Las únicas dependencias activas:

| Variable | Uso |
|---|---|
| `FMP_API_KEY` | Histórico OHLC |
| `TASTYTRADE_CLIENT_ID` | OAuth tastytrade (option chain + dxFeed quotes) |
| `TASTYTRADE_CLIENT_SECRET` | OAuth tastytrade |
| `TASTYTRADE_REFRESH_TOKEN` | OAuth tastytrade |
| `ANTHROPIC_API_KEY` | Interpretación LLM del skew |
| `ANTHROPIC_MODEL` | Opcional. Default `claude-sonnet-4-5-20250929` |

**Nota crítica sobre el key de Anthropic:** `python-dotenv` solo lee `.env`
al startup. Si el key cambia (por rotación o regeneración), hay que reiniciar
el proceso de Streamlit para que el nuevo key se cargue. En Render basta
con redesployar el servicio.

### 3.2 · Cache invalidation del LLM

El cache de las interpretaciones vive en `st.session_state["_skew_cache"]`,
hasheado por el dict del payload. Si el prompt cambia (como acaba de pasar),
las interpretaciones cacheadas en sesiones activas siguen mostrando el output
viejo. Solución: refresh completo del navegador (no "Rerun") para limpiar
session_state.

### 3.3 · Comandos útiles

```bash
# Reiniciar streamlit local con .env nuevo
pkill -f "streamlit run app.py"
.venv/bin/streamlit run app.py --server.port 8501

# Validar sintaxis tras edits
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
python3 -c "import ast; ast.parse(open('modules/plots.py').read()); print('OK')"

# Verificar que el ANTHROPIC_API_KEY responde
set -a && source .env && set +a
curl -s -H "x-api-key: $ANTHROPIC_API_KEY" \
     -H "anthropic-version: 2023-06-01" \
     -H "content-type: application/json" \
     -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":20,"messages":[{"role":"user","content":"reply ok"}]}' \
     https://api.anthropic.com/v1/messages
```

---

## 4 · Pendientes (heredados de 2026-05-08)

Los 9 items de polish del documento OAuth siguen pendientes, no se tocaron:

1. Bug Stripe naive vs aware datetime
2. Rotar GitHub PAT
3. Limpiar variables tastytrade muertas
4. Deprecar `scripts/renew_tt_token.py`
5. Migrar `fetch_options_snapshot` y `get_quotes` a async nativos
6. Mover `compute_rnd_from_calls` a `asyncio.to_thread`
7. Logging estructurado para OAuth
8. Modo mock para tests de integración
9. Preparar repo para merge en `prob-edge-api`

---

## 5 · Referencias

- `app.py` — render_densidades + helpers LLM (`_compute_skew_payload`,
  `_stream_skew_interpretation`, `_render_skew_box`).
- `modules/plots.py` — `plot_main_figure` con heatmap diverging, callouts de
  rango de confianza, density-peak strikes con PoP, skew score.
- `docs/2026-05-08-oauth-migration.md` — fix de auth de tastytrade vía OAuth
  Personal Grant (paso anterior, sin el cual nada de esto funcionaba).
