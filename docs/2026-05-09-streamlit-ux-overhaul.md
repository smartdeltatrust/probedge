# Streamlit UX overhaul — Densidades tab (2026-05-09)

Documento canónico del estado actual de la pestaña Densidades de la app
Streamlit (`app.py`) y del pipeline de plotting (`modules/plots.py`) tras
la sesión de overhaul del 2026-05-09. Continuación natural del fix de
autenticación OAuth documentado en `2026-05-08-oauth-migration.md`.

Este doc está pensado para que cualquier LLM (o humano) que retome el
proyecto entienda en una lectura **qué hace cada superficie del UI, qué
matemática hay detrás de cada visualización, y dónde están los puntos de
extensión**. Si vas a modificar algo, leelo entero antes.

---

## 1 · Resumen ejecutivo

La app pasó de tener controles dispersos debajo del chart, un cono con
callouts ambiguos y zero análisis textual a un layout fintech moderno
estilo tastytrade/Bloomberg con:

- **Sidebar consolidado** con todos los controles (ticker, range, expiry,
  past_days, r_rate, heatmap toggle).
- **Chart central** full-width, height 760 px, con eje Y derecho, callouts
  de rangos de confianza, density-peak strikes con PoP, heatmap diverging
  del skew, y skew score numérico.
- **Interpretación LLM (Claude streaming)** con typewriter effect y prompt
  de dos párrafos enfocado en venta de prima + análisis específico de
  short put con tail risks.
- **Tabla PoP** heatmap-styled como referencia rápida de premium-selling
  para varios niveles del CDF.
- **Tipografía fintech moderna** (Inter + JetBrains Mono) con alternates
  matemáticos.

El default ticker es **SPY**.

---

## 2 · Cambios por superficie

### 2.1 · Selector de expiry con DTE (`app.py`)

`format_func` en el `selectbox` para mostrar `2026-06-19  ·  42 DTE`
mientras el value subyacente sigue siendo `YYYY-MM-DD` (compatible con
el resto del pipeline).

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
   (`pd.date_range(..., freq="B")`) ausentes de `quotes_df["Date"]`. Captura
   Thanksgiving, 4 de julio, etc., sin hardcodear calendario.

Tanto las velas históricas como la cinta del cono se compactan sin huecos.

### 2.3 · Eje Y al lado derecho (Bloomberg/tastytrade)

```python
fig.update_yaxes(side="right", ticks="inside", ticklen=4,
                 tickcolor="#333333", title_standoff=12)
```

Colorbar del heatmap movido a `x=-0.05` (lado izquierdo) con `thickness=10`
para que no choque con el nuevo eje Y derecho.

### 2.4 · Forward window auto = DTE

El slider "Forward window (days)" desapareció. Ahora se computa como:

```python
future_days = max(7, int((expiry_date - valuation_date).days))
```

Después de parsear el expiry. El cono termina exactamente en la fecha de
vencimiento elegida; un control menos para el usuario.

### 2.5 · Dividend yield removido del UI

`q_rate = 0.0` como constante. Era el default que el usuario nunca tocaba.
Si en el futuro se necesita exponerlo (e.g., para single names con alto
yield), está limpio de agregar.

### 2.6 · Cone callouts: rangos de confianza

Los 5 callouts en el extremo derecho del cono pasaron de mostrar
percentiles cumulativos / probabilidades de cola a mostrar **bordes de los
rangos de confianza** (la lectura natural del cono):

| Antes (CDF) | Antes (exceedance) | **Ahora** |
|---|---|---|
| `97.5%  $510` | `↑$510  2.5%` | **`95%↑ $510`** |
| `84%    $475` | `↑$475  16%`  | **`68%↑ $475`** |
| `50%    $450` | `med $450`    | **`med $450`** |
| `16%    $425` | `↓$425  16%`  | **`68%↓ $425`** |
| `2.5%   $390` | `↓$390  2.5%` | **`95%↓ $390`** |

**Lectura:** *"el rango entre `95%↓` y `95%↑` contiene al subyacente con
95 % de confianza al vencimiento"*. El skew sigue siendo visible en la
asimetría de las distancias `med → 95%↑` vs `med → 95%↓`.

Hover de las bandas también re-etiquetado al mismo lenguaje
(`95%↑ $X`, `68%↓ $X`, etc.).

### 2.7 · Heatmap rediseñado: diverging por mediana

**Antes:** colorscale azul único (denso → claro). La dispersión natural
`~√T` hacía que los tails siempre se vieran oscuros y se perdía la forma
del skew a horizontes largos.

**Ahora:** un único heatmap diverging:

```python
z_norm = density / col_max  # normalizado por columna
sign = np.where(price >= median_per_col, +1, -1)
z_signed = z_norm * sign  # rango [-1, +1]
```

- `zmin=-1, zmax=+1`
- Colorscale verde (upside) → transparente (cero) → rojo (downside)
- Normalización por columna preserva la **forma** del slice en todo el
  horizonte (no la magnitud absoluta, que decae con T)
- **Asimetría visible**: si el plumaje rojo se extiende más lejos que el
  verde → cola izquierda gorda → skew típico de equity (negativo)

### 2.8 · Skew score numérico (esquina superior izquierda)

Métrica de Pearson basada en cuantiles, calculada al vencimiento:

```
skew = ((q97.5 − q50) − (q50 − q2.5)) / (q97.5 − q2.5)
```

Acotada en `[−1, +1]`. Etiqueta dinámica en una anotación HTML en la
esquina superior izquierda:

- `< −0.05` → "downside-heavy" (rojo `#ff5a82`)
- `> +0.05` → "upside-heavy" (verde `#00ffc8`)
- entre medias → "near-symmetric" (gris)

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
expire OTM. Color verde si está sobre la mediana, rojo si debajo.
Etiquetas posicionadas dentro del cono al extremo terminal junto a los
callouts del rango de confianza (`xref="x", xanchor="right", xshift=-4`).

### 2.10 · Hover enriquecido en el heatmap

Cada celda lleva en `customdata` un string formateado con:

- Flecha direccional (`↑` / `↓`) según el lado de la mediana.
- Probabilidad de exceedance: `P(S_T ≥ K)` arriba, `P(S_T ≤ K)` abajo.
- Rango de confianza que contiene la celda: `inside 68% cone`,
  `inside 95% cone`, o `outside 95% (tail)`.

Hovertemplate: `$%{y:.2f} · %{customdata}<extra>RND skew</extra>`. Con
`hovermode="x unified"`, al pasar el mouse por una fecha se ven
simultáneamente:

- Valores de las bandas del cono a esa fecha.
- Celda exacta del heatmap bajo el cursor.

### 2.11 · Default ticker SPY

`app.py:889` (sidebar global). El selector de Densidades hereda del
global, por lo que SPY es el default real ahora.

### 2.12 · Layout: sidebar consolidado, chart al centro

**Antes:** sidebar con un solo ticker para fundamentales + caption
explicando que "el tab Densidades tiene su propio selector"; controles
del chart abajo; chart en altura por defecto de Plotly (~450 px).

**Ahora — sidebar (top→bottom):**

1. `## ProbEdge` (título)
2. **Ticker** (uno solo, default SPY) — drives todos los tabs
3. `Densidades` (subhead)
4. Historical range
5. Expiry (con DTE format)
6. Historical window
7. Risk-free rate
8. Show density heatmap (toggle)
9. Pie: `Data: FMP · tastytrade · Anthropic Claude`

**Centro del tab Densidades:**

1. Subheader + caption "Data: ..."
2. **Chart full-width (height 760 px)**
3. *(divider)*
4. **Skew interpretation** (LLM streaming, recuadro cyan tenue)
5. *Disclaimer †*
6. *(divider)*
7. **PoP table** (heatmap-styled DataFrame)
8. *Caption explicativa*
9. *(divider)*
10. "Explanation & Math" estático

Eliminado el widget separado `dens_ticker` — se unificó con el global.
Los containers `chart_container`, `below_chart_container`,
`controls_container` ya no existen.

### 2.13 · Altura del chart aumentada (760 px)

`fig.update_layout(height=760)`. Default de Plotly era ~450 px. Las
proporciones de los datos se preservan (los ejes son lineales en X/Y;
solo cambian los pixeles disponibles).

### 2.14 · Interpretación LLM con typewriter streaming

Bloque después del chart, antes del "Explanation" estático.

**Pipeline:**

1. **`_compute_skew_payload(K_grid, pdf_K, ticker, spot, expiry_date, dte)`**
   extrae stats clave del RND al vencimiento: spot, q2.5/q16/q50/q84/q97.5,
   skew score, top-5 dense strikes con PoP. Devuelve dict serializable.

2. **`_stream_skew_interpretation(payload_json, model)`** es un generator
   que yieldea text deltas desde la Anthropic streaming API
   (`client.messages.stream(...)`). Antes de llamar, valida tres
   precondiciones con mensajes específicos:
   - Paquete `anthropic` no instalado.
   - `ANTHROPIC_API_KEY` no configurada.
   - Cliente no disponible por otra causa.

3. En `render_densidades`, el call site:
   - Computa hash MD5 del payload.
   - Si el hash está en `st.session_state["_skew_cache"]`, render estático.
   - Si no, abre `st.empty()` placeholder y va re-renderizando el div en
     cada chunk del stream (efecto typewriter ChatGPT).
   - Guarda el texto final en session_state cache para el resto de la
     sesión.

**Estilo del recuadro (`_render_skew_box`):**

```css
background-color: rgba(0, 180, 220, 0.04);    /* cyan tenue */
border-left: 3px solid rgba(0, 180, 220, 0.35); /* acento 3px */
font-family: 'Inter', sans-serif;             /* prosa moderna */
font-size: 13.5px;
line-height: 1.7;
letter-spacing: 0.005em;
color: #cccccc;
```

**Sanitización defensiva:** antes de renderizar, escape de `\\ $ * _ \``
para evitar que Streamlit interprete el texto como LaTeX/Markdown. Doble
linea (`\n\n`) → `<br><br>` para preservar separación de párrafos;
newlines simples → espacios.

**Disclaimer:** `st.caption()` con prefijo `†` (footnote tradicional
financiera, en lugar de `⚠️` por elegancia):

> † This interpretation is AI-generated commentary on a risk-neutral
> probability study derived from option-chain prices. It is not financial
> advice nor a recommendation to trade — use at your own risk.

### 2.15 · Prompt de Claude: dos párrafos, foco en venta de prima

El prompt está marcado con divisores `━━━` en `_stream_skew_interpretation`
y construido dinámicamente con el dict del payload. Estructura forzada:

**§1 — Premium-selling overview** (2-4 oraciones):

- Dirección y magnitud del skew → cuál lado del chain (puts o calls)
  carga la prima más rica.
- El strike más atractivo de la lista densa para vender prima, con su PoP,
  etiquetado como short-put o short-call según mediana.

**§2 — Short-put deep dive** (siempre presente, 2-4 oraciones):

- Strike concreto para cash-secured put (de la lista densa < mediana, o
  fallback a q16/q2.5).
- Por qué tiene sentido (cushion, densidad de soporte, PoP).
- **Tail risks**: % de caída desde spot necesaria para tocar el strike +
  uno o dos escenarios extremos (sell-off, earnings shock, evento macro).

**Reglas estrictas en el prompt:**

- Plain text, sin headings, bullets, listas, asteriscos, underscores,
  backticks, tablas, emojis, ni Markdown.
- Sin `$`. Precios como `USD 510.20`.
- Exactamente 2 párrafos separados por una línea en blanco.
- Sin labels de párrafo ("Paragraph 1/2"), solo flowing.
- Sin empezar con el ticker o un heading.
- Sin jargon que un retail trader no pueda entender.

### 2.16 · Tabla PoP — referencia para venta de prima

Debajo del LLM block, `_build_pop_table(K_grid, pdf_K, spot)` genera un
DataFrame de 11 filas con strikes sampleados a niveles fijos del CDF:

```
levels = [0.05, 0.10, 0.16, 0.25, 0.35, 0.50, 0.65, 0.75, 0.84, 0.90, 0.95]
```

Para cada nivel `lvl`:

- `K = K_grid[searchsorted(cdf, lvl)]` — strike donde CDF alcanza lvl.
- `Call PoP = lvl × 100` — `P(S_T ≤ K)`, prob short call OTM.
- `Put PoP = (1 − lvl) × 100` — `P(S_T ≥ K)`, prob short put OTM.
- `Δ spot = (K − spot) / spot × 100`.

Render con `_render_pop_table(df)` usando Pandas Styler:

```python
pop_cmap = LinearSegmentedColormap.from_list(
    "ttrade_pop",
    ["#ff3366", "#3a3a3a", "#00d4aa"],  # rojo→gris→verde
)
df.style.background_gradient(cmap=pop_cmap, subset=["Call PoP"], vmin=0, vmax=100)
       .background_gradient(cmap=pop_cmap, subset=["Put PoP"],  vmin=0, vmax=100)
       .format({...})
```

Display: `st.dataframe(styled, use_container_width=True, hide_index=True)`.

**Lectura:**

- Strikes bajos (top de la tabla): Call PoP rojo (call ITM, peligroso de
  vender) + Put PoP verde intenso (put OTM, cash-secured put seguro).
- Strikes altos (bottom): inverso — Call PoP verde (covered call seguro),
  Put PoP rojo.
- Mediana (50 %): ambos en gris/neutro.

Coherencia visual: el cmap usa los **mismos colores** que las velas y el
heatmap del skew (verde tastytrade `#00d4aa`, rojo tastytrade `#ff3366`).

### 2.17 · Tipografía fintech moderna

CSS injection en `st.markdown` justo después de `set_page_config`:

```python
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
```

**Distribución por superficie:**

| Elemento | Fuente | Spirit |
|---|---|---|
| Título del chart | **Inter** 16px | Heading limpio |
| Ejes / ticks / hovers / annotations / callouts | **JetBrains Mono** | Datos numéricos, alineación matemática |
| Skew interpretation (prosa) | **Inter** 13.5px | Texto fluido, legible |
| PoP table (DataFrame) | **JetBrains Mono** (vía CSS) | Números alineados |
| Sidebar / labels / botones / Streamlit UI | **Inter** | Look moderno fintech |

`font-feature-settings` activado:

- Inter: `cv11`, `ss01`, `ss03` (alternates más legibles).
- JetBrains Mono: `calt` (ligatures `>=`, `==`), `zero` (cero cortado),
  `ss01` (estilo programador).

Captions con `letter-spacing: 0.01em` para look financiero más espaciado.

Plotly usa fallback chain `'JetBrains Mono', Consolas, monospace`. Si
Google Fonts falla por red, cae a Consolas (lo de antes). El Render no
necesita configuración — Google Fonts se carga client-side.

---

## 3 · Operacional

### 3.1 · Variables de entorno

Sin cambios respecto al estado pre-overhaul:

| Variable | Uso | Notas |
|---|---|---|
| `FMP_API_KEY` | Histórico OHLC | requerido |
| `TASTYTRADE_CLIENT_ID` | OAuth tastytrade | OAuth Personal Grant |
| `TASTYTRADE_CLIENT_SECRET` | OAuth tastytrade | OAuth Personal Grant |
| `TASTYTRADE_REFRESH_TOKEN` | OAuth tastytrade | no expira |
| `ANTHROPIC_API_KEY` | Interpretación LLM | requerido para skew interpretation |
| `ANTHROPIC_MODEL` | Modelo de Claude | opcional, default `claude-sonnet-4-5-20250929` |

**Variables muertas** (legado pre-OAuth, se pueden remover):
`TASTYTRADE_LOGIN`, `TASTYTRADE_PASSWORD`, `TASTYTRADE_TOKEN`,
`TASTYTRADE_REMEMBER_TOKEN`, `TASTYTRADE_SESSION_TOKEN`.

### 3.2 · Dependencias críticas

`requirements.txt` ahora incluye:

```
anthropic>=0.40
matplotlib>=3.7  # ya estaba; usado para LinearSegmentedColormap del PoP table cmap
```

**Trampa documentada:** local funcionaba con `anthropic` instalado en el
`.venv` desde experimentos previos pero NO estaba declarado en
`requirements.txt`. Render fallaba con un mensaje engañoso
("ANTHROPIC_API_KEY no configurada") cuando el problema real era el
import fail. Resuelto en commit `578f114`.

### 3.3 · Cache invalidation del LLM

El cache vive en `st.session_state["_skew_cache"]`, hasheado por
`hashlib.md5(json.dumps(payload, sort_keys=True))`.

- Cambia el payload (ticker, expiry, controles del cono) → hash distinto
  → llamada nueva a Anthropic.
- Refresh completo del navegador (no "Rerun") limpia session_state.
- Si se modifica el prompt en el código pero el payload sigue igual, el
  cache devuelve la respuesta vieja → hay que refresh.

### 3.4 · Reinicio de Streamlit y `.env`

`python-dotenv` lee `.env` SOLO al startup del proceso. Si rotan
`ANTHROPIC_API_KEY` u otro secret:

- **Local:** `pkill -f "streamlit run"` y volver a correr.
- **Render:** redeploy / restart del servicio.

Un mero "Rerun" desde la UI no recarga el `.env` — el proceso sigue con
los secrets viejos en memoria.

### 3.5 · Comandos útiles

```bash
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

# Reiniciar Streamlit local
pkill -f "streamlit run app.py"
.venv/bin/streamlit run app.py --server.port 8501

# Verificar streamlit health
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_stcore/health
```

---

## 4 · Mapa de helpers / funciones nuevas

```
app.py
├── set_page_config + CSS injection (fonts globales)            [línea ~36]
├── cached_quotes / cached_expiries / cached_options           [Densidades data]
├── _build_pop_table(K_grid, pdf_K, spot) → DataFrame           [§2.16]
├── _render_pop_table(df) → st.dataframe styled                [§2.16]
├── _compute_skew_payload(K_grid, pdf_K, ticker, ...) → dict   [§2.14]
├── _stream_skew_interpretation(payload_json, model) → gen[str] [§2.14]
├── _render_skew_box(text) → str (HTML)                        [§2.14]
├── render_densidades(ticker)                                  [tab Densidades]
└── main()                                                     [entry point]

modules/plots.py
└── plot_main_figure(quotes_df, dates_all, price_grid, density,
                     expiry_dates, valuation_date,
                     show_heatmap, show_past_rnd)
    ├── compute_quantile_bands(price_grid, density)            [helper math]
    ├── Heatmap diverging por mediana (§2.7)
    ├── Skew score annotation (§2.8)
    ├── Cone callouts de rango de confianza (§2.6)
    ├── Density-peak strikes con PoP (§2.9)
    ├── Hover enriquecido (§2.10)
    ├── Eje Y derecho (§2.3)
    └── Tipografía Inter+JetBrains Mono (§2.17)
```

---

## 5 · Convenciones matemáticas centrales

Toda la app gira en torno a la **densidad risk-neutral (RND)** extraída
del option chain via Breeden-Litzenberger:

```
f(K) = ∂²C / ∂K²    (Breeden-Litzenberger 1978)
```

donde `C(K, T)` es el precio de un call europeo en strike `K` y madurez
`T`. La RND `f(K)` ya **incorpora todo el skew de IV** — cualquier
asimetría en la sonrisa (puts más caros que calls → cola izquierda gorda)
está bakeada en `f(K)`. No hay aproximación lognormal en ningún lado.

A partir de `f(K)`:

- **CDF** `F(K) = ∫₀ᴷ f(x)dx` (vía `cumsum * dx`, normalizada a 1).
- **Quantiles** `q_p = F⁻¹(p)` (vía `searchsorted`).
- **Exceedance prob** `P(S_T ≥ K) = 1 − F(K)` para K > median;
  `P(S_T ≤ K) = F(K)` para K < median.
- **PoP** (Probability of Profit, tastytrade-style)
  `= max(F(K), 1 − F(K))` — prob de que un OTM short en `K` expire OTM.
- **Skew score** (Pearson quantile)
  `= ((q97.5 − q50) − (q50 − q2.5)) / (q97.5 − q2.5)`.

**Invariante crítica del proyecto:** `compute_rnd_from_calls` /
`compute_rnd_from_clean_calls` deben producir una densidad cuya integral
sobre `price_grid` sea ≈ 1.0. El test
`tests/test_api.py::test_rnd_is_valid_density` enforza
`|integral − 1| < 0.05` y es el regression guard más importante. No
toques nada en el pipeline (`_clean_calls_from_chain`, PCHIP
interpolation, forward correction) sin verificar este test.

---

## 6 · Pendientes (heredados, sin tocar en este overhaul)

Los 9 items del documento OAuth siguen pendientes:

1. Bug Stripe naive vs aware datetime (`api/billing/models.py:39`).
2. Rotar GitHub PAT (expuesto en `.git/config` antes).
3. Limpiar variables tastytrade muertas del `.env` y Render.
4. Deprecar `scripts/renew_tt_token.py`.
5. Migrar `fetch_options_snapshot` y `get_quotes` a async nativos.
6. Mover `compute_rnd_from_calls` a `asyncio.to_thread` en handlers.
7. Logging estructurado para OAuth (telemetría).
8. Modo mock para tests de integración (`RND_TEST_MODE=mock`).
9. Preparar repo para merge en `prob-edge-api` (ver
   `docs/superpowers/specs/2026-04-29-prob-edge-roadmap-design.md` en
   workspace root).

---

## 7 · Referencias

- `app.py` — render_densidades + helpers LLM + helpers PoP table.
- `modules/plots.py` — `plot_main_figure` con todo el sistema visual.
- `docs/2026-05-08-oauth-migration.md` — fix de auth de tastytrade vía
  OAuth Personal Grant (paso anterior, sin el cual nada funciona).
- `metodologia.md` (root del repo) — la matemática del Breeden-Litzenberger
  pipeline en detalle.
- `PROJECT_SPEC.md` §4 (root del repo) — spec section-level del pipeline RND.

---

## 8 · Commits clave de este overhaul

```
8f15a6d  feat: Densidades UX overhaul (sidebar, cone, heatmap, LLM)
578f114  fix: agregar anthropic a requirements + mensajes de error específicos
78bf7b5  feat: tipografía moderna fintech (Inter + JetBrains Mono) + tabla PoP
```

Cualquier commit posterior a `78bf7b5` que toque la pestaña Densidades
debería actualizar este documento.
