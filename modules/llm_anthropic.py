# src/adapters/llm_anthropic.py
from __future__ import annotations

import os
from typing import Any, Generator, Optional, TYPE_CHECKING, Dict
from typing import Dict, List, Generator, Optional
import math



if TYPE_CHECKING:
    from anthropic import Anthropic as AnthropicClient  # typing only
else:
    AnthropicClient = Any

try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None


def get_anthropic_client() -> Optional[AnthropicClient]:
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key or Anthropic is None:
        return None
    return Anthropic(api_key=api_key)


def get_anthropic_model(default: str = "claude-sonnet-4-5-20250929") -> str:
    return (os.getenv("ANTHROPIC_MODEL") or default).strip()


def stream_translate_and_summarize(
    *,
    english_text: str,
    sector: str,
    model: str,
    max_tokens: int = 700,
    temperature: float = 0.4,
    max_words: int = 90,
) -> Generator[str, None, None]:
    """
    Devuelve chunks de texto (delta) en streaming.

    - Traduce y sintetiza a español (México)
    - Un solo párrafo
    - Máximo `max_words` palabras (instrucción al modelo)
    - Sin em-dashes, sin viñetas, sin títulos
    """
    english_text = (english_text or "").strip()
    if not english_text:
        yield ""
        return

    client = get_anthropic_client()
    if client is None:
        # Sin cliente, devolvemos vacío para que UI haga fallback.
        yield ""
        return

    sector = (sector or "No especificado").strip() or "No especificado"
    model = (model or get_anthropic_model()).strip()

    system = (
        "Eres un traductor y editor profesional de textos corporativos. "
        "Escribes español de México, claro, natural y conciso, con vocabulario financiero sobrio. "
        "Puedes usar ironía muy ligera, sarcasmo agresivo. "
        "No uses em dashes, no uses viñetas, no uses títulos, entrega un solo párrafo. "
        "No agregues texto antes ni después del párrafo. "
        "No inventes información, si algo no está explícito en el texto fuente, no lo afirmes."
    )

    user = (
        f"Traduce y sintetiza el siguiente texto en un solo párrafo de máximo {max_words} palabras. "
        "Enfócate en el producto o servicio principal. "
        "Menciona la monetización usando la frase exacta: "
        "\"su fuente principal de ingresos proviene de:\". "
        "Evita fechas, fundadores y afirmaciones no presentes en el texto. "
        f"Cierra el párrafo indicando el sector del S&P 500: {sector}. "
        "\n\nTEXTO:\n"
        f"{english_text}"
    )

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user}],
                }
            ],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta.text
                    if delta:
                        yield delta
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        # Si falla la API, devolvemos vacío y la UI hace fallback al inglés
        yield ""


def stream_valuation_from_multiples(
    *,
    symbol: str,
    multiples: Dict[str, object],
    shares: Optional[Dict[str, object]] = None,
    quote: Optional[Dict[str, object]] = None,  # NUEVO: precio desde quoteService.py
    model: str,
    max_tokens: int = 1750,        # optimizado para max_chars=1500
    temperature: float = 0.65,    # más obediente a formato, menos varianza
    max_chars: int = 1250,
) -> Generator[str, None, None]:
    """
    Streaming de Anthropic para evaluar valuación con base en múltiplos.

    Reglas de salida (se le piden al modelo y se refuerzan con corte local):
    - Máximo ~max_chars caracteres
    - Sin bullets, tablas, títulos o subtítulos
    - No usar símbolo $, usar USD
    - Divide en párrafos para móvil
    - Usa shares-float si se provee
    - Usa precio si se provee (quote)
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    symbol = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    def to_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            x = float(v)
            if math.isnan(x) or math.isinf(x):
                return None
            return x
        except Exception:
            return None

    def fmt_num(v: object, decimals: int = 2) -> str:
        x = to_float(v)
        if x is None:
            return "N/D"
        try:
            return f"{x:,.{decimals}f}"
        except Exception:
            return str(v)

    def fmt_int(v: object) -> str:
        x = to_float(v)
        if x is None:
            return "N/D"
        try:
            return f"{int(round(x)):,}"
        except Exception:
            return str(v)

    def fmt_ratio(v: object) -> str:
        # Para múltiplos tipo 16.7, 11.9, etc
        return fmt_num(v, decimals=2)

    def fmt_usd(v: object) -> str:
        # Para valores grandes en USD, sin abreviar para evitar ambigüedad
        x = to_float(v)
        if x is None:
            return "N/D"
        if abs(x) >= 1_000_000:
            return f"{x:,.0f}"
        return f"{x:,.2f}"

    def fmt_pct(v: object) -> str:
        """
        Heurística segura:
        - Si viene como 0.25 lo interpreta como 25.00%
        - Si viene como 25 lo interpreta como 25.00%
        """
        x = to_float(v)
        if x is None:
            return "N/D"
        x_scaled = x * 100.0 if abs(x) <= 1.5 else x
        return f"{x_scaled:,.2f}%"

    shares = shares or {}
    quote = quote or {}

    free_float = shares.get("freeFloat") or shares.get("free_float") or shares.get("free_float_pct")
    float_shares = shares.get("floatShares") or shares.get("float_shares")
    outstanding = shares.get("outstandingShares") or shares.get("outstanding_shares")
    shares_date = shares.get("date")

    # Extrae precio desde quote (ajusta llaves según tu quoteService.py)
    price_usd = (
        quote.get("price")
        or quote.get("last")
        or quote.get("lastPrice")
        or quote.get("c")          # algunas APIs usan 'c' close
        or quote.get("close")
    )
    price_date = quote.get("date") or quote.get("asOf") or quote.get("timestamp") or quote.get("datetime")

    # Derivaciones útiles, sin inventar datos
    mcap = to_float(multiples.get("marketCap"))
    graham = to_float(multiples.get("grahamNumberTTM"))
    price = to_float(price_usd)
    out_shares = to_float(outstanding)

    shares_est_from_price = None
    if mcap is not None and price is not None and price > 0:
        shares_est_from_price = mcap / price

    implied_price_from_outstanding = None
    if mcap is not None and out_shares is not None and out_shares > 0:
        implied_price_from_outstanding = mcap / out_shares

    premium_vs_graham_pct = None
    if price is not None and graham is not None and graham > 0:
        premium_vs_graham_pct = (price / graham - 1.0) * 100.0

    shares_block = (
        "Datos de float y liquidez (shares-float):\n"
        f"freeFloat: {fmt_pct(free_float)}\n"
        f"floatShares: {fmt_int(float_shares)}\n"
        f"outstandingShares: {fmt_int(outstanding)}\n"
        f"date: {shares_date or 'N/D'}\n\n"
    )

    quote_block = (
        "Datos de precio (quote):\n"
        f"precioActualUSD: {fmt_usd(price)} USD\n"
        f"fechaPrecio: {price_date or 'N/D'}\n"
        f"precioImplicitoPorMarketCapYOutShares: {fmt_usd(implied_price_from_outstanding)} USD\n"
        f"accionesAproxPorMarketCapYPrecio: {fmt_int(shares_est_from_price)}\n"
        f"primaVsNumeroDeGrahamPct: {fmt_pct(premium_vs_graham_pct)}\n\n"
    )

    user_text = (
        f"Con los datos disponibles, emite un dictamen de valoración para {symbol} usando SOLO la información dada.\n\n"
        f"Datos (USD y múltiplos):\n"
        f"marketCap: {fmt_usd(multiples.get('marketCap'))} USD\n"
        f"enterpriseValueTTM: {fmt_usd(multiples.get('enterpriseValueTTM'))} USD\n"
        f"evToSalesTTM: {fmt_ratio(multiples.get('evToSalesTTM'))}\n"
        f"evToOperatingCashFlowTTM: {fmt_ratio(multiples.get('evToOperatingCashFlowTTM'))}\n"
        f"evToFreeCashFlowTTM: {fmt_ratio(multiples.get('evToFreeCashFlowTTM'))}\n"
        f"evToEBITDATTM: {fmt_ratio(multiples.get('evToEBITDATTM'))}\n"
        f"numeroDeGrahamTTM: {fmt_usd(multiples.get('grahamNumberTTM'))} USD por acción\n"
        f"grahamNetNetTTM: {fmt_usd(multiples.get('grahamNetNetTTM'))}\n\n"
        f"{quote_block}"
        f"{shares_block}"
        "Prohibiciones estrictas: NO escribas títulos, NO uses Markdown (no #, no **, no *), "
        "NO uses etiquetas como 'Veredicto:' o 'Conclusión:'. No escribas una línea introductoria tipo 'Valuación por múltiplos'. "
        "La primera letra de tu respuesta debe ser parte de la primera oración.\n\n"
        "Formato obligatorio para móvil: escribe EXACTAMENTE 4 párrafos, separados por una línea en blanco (dos saltos de línea \\n\\n). "
        "Cada párrafo debe tener 1 o 2 oraciones, y cada oración debe ser corta pero con la idea principal completa y explicativa.\n\n"
        "Reglas de inferencia: no inventes comparables ni promedios sectoriales. Si el precioActualUSD es N/D, omítelo en el análisis, pero si está presente NO lo pidas.\n\n"
        "Contenido por párrafo (no copies etiquetas):\n"
        "Párrafo 1: dictamen (sobrevaluada o subvaluada) y confianza (alta, media o baja) Una exageración de buen gusto con expresión whitexican.\n"
        "Párrafo 2: EV/Ventas (El Valor de la Empresa respecto de las Ventas, EV/Ventas) y EV/EBITDA "
        "(El Valor de la Empresa respecto de sus utilidades antes de impuestos, depreciación y amortización, EV/EBITDA), "
        "qué descuentan sobre crecimiento y márgenes, con reformulación cotidiana mexicana.\n"
        "Párrafo 3: Número de Graham, usa primaVsNumeroDeGrahamPct (si no es N/D) para decir prima o descuento y contrasta con visión moderna, sin anglicanismos.\n"
        "Párrafo 4: Dictamen del análisis con una implicación práctica y una exageración con buen humor que permita elevar la confianza (industria, guidance o crecimiento esperado).\n\n"
        "Estilo: español mexicano, profesional y eficiente, humor irónico elegante y sobrio, máximo dos comparaciones cotidianas en toda la respuesta, "
        "evita la arrogancia.\n\n"
        f"Restricciones finales: máximo {max_chars} caracteres. No uses emojis ni caracteres especiales. No uses el símbolo de $, escribe USD."
    )

    system = (
        "Eres un analista financiero con certificación CFA, 10+ años valuando empresas públicas. "
        "Priorizas claridad, disciplina de datos y consistencia lógica. "
        "Usas solo la información proporcionada, no inventas cifras ni rangos de industria. "
        "Si se provee precioActualUSD lo usas para interpretar el Número de Graham y no lo solicitas. "
        "Si se proveen datos de shares-float (outstandingShares, floatShares, freeFloat), los interpretas cualitativamente "
        "sin inventar umbrales de industria ni comparables. "
        "Respondes en texto plano, con párrafos, sin títulos, sin Markdown."
    )

    emitted = 0
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = getattr(event.delta, "text", "")
                    if not delta:
                        continue

                    # Corte duro por max_chars para que tu UI nunca muestre más de lo permitido
                    remaining = max_chars - emitted
                    if remaining <= 0:
                        break

                    if len(delta) > remaining:
                        yield delta[:remaining]
                        emitted += remaining
                        break

                    yield delta
                    emitted += len(delta)

                elif getattr(event, "type", "") == "message_stop":
                    break

    except Exception:
        yield ""



from typing import Dict, Generator

def stream_returns_analysis(
    *,
    symbol: str,
    returns: Dict[str, object],
    model: str,
    max_tokens: int = 900,
    temperature: float = 0.7,
    max_chars: int = 1200,
) -> Generator[str, None, None]:
    """
    Análisis de rentabilidad y retornos (Segmento B).
    Texto sin títulos, sin bullets, sin tablas, máximo ~max_chars caracteres.
    Incluye reformulación simple tras cada idea técnica.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    symbol = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    def fmt_pct(v: object) -> str:
        if v is None:
            return "N/D"
        try:
            x = float(v)
            return f"{x*100:.2f}%"
        except Exception:
            return str(v)

    user_text = (
        f"Analiza la rentabilidad y retornos de {symbol} usando estas métricas TTM:\n\n"
        f"returnOnAssetsTTM: {fmt_pct(returns.get('returnOnAssetsTTM'))}\n"
        f"returnOnEquityTTM: {fmt_pct(returns.get('returnOnEquityTTM'))}\n"
        f"returnOnTangibleAssetsTTM: {fmt_pct(returns.get('returnOnTangibleAssetsTTM'))}\n"
        f"returnOnInvestedCapitalTTM: {fmt_pct(returns.get('returnOnInvestedCapitalTTM'))}\n"
        f"returnOnCapitalEmployedTTM: {fmt_pct(returns.get('returnOnCapitalEmployedTTM'))}\n"
        f"operatingReturnOnAssetsTTM: {fmt_pct(returns.get('operatingReturnOnAssetsTTM'))}\n"
        f"earningsYieldTTM: {fmt_pct(returns.get('earningsYieldTTM'))}\n"
        f"freeCashFlowYieldTTM: {fmt_pct(returns.get('freeCashFlowYieldTTM'))}\n\n"
        "Explica qué dicen estos retornos sobre eficiencia operativa, capacidad de reinversión y calidad del retorno. "
        "Señala consistencias o banderas rojas o verdes respecto de los números, por ejemplo ROE muy elevado frente a ROA, y explica en términos simples qué podría implicar "
        "En términos de apalancamiento o base de capital, sin inventar datos, explica de manera simple los indicadores en español, por ejemplo ROA (Return on Assets)  Retorno sobre los activos \n\n"
        "Después de cada explicación técnica reformula la idea para que la entienda cualquier persona con un lenguaje práctico que hasta un niño de 12 años con ejemplos simples lo pueda entender, puedes usar exageraciones con humor fino y elegante."
        "Sin sonar condescendiente, ni mencionar que tu conclusión es para alguien con entendimiento menor,  evita poner frases como: Conclusión para un niño de doce años; sé empático y redacta tu conclusión en el último párrafo de forma simple y contundente"
        f"Limita tu respuesta a {max_chars} caracteres, no uses bullets, tablas, títulos o subtítulos, evita emojis y caracteres especiales."
    )

    system = (
        "Asume el rol de un analista financiero con certificación CFA y más de 10 años de experiencia. "
        "Escribe en español profesional y claro. "
        "Conecta métricas relacionadas (ROA vs ROE, ROIC/ROCE, operating ROA, yields). "
        "No inventes comparables ni promedios industriales, si no se te proporcionan. Usa exclusivamente los datos presentados"
        "Si una métrica es extrema, explica por qué puede ocurrir y qué datos adicionales se necesitarían para confirmarlo."
        "Asume la personalidad de un Whitexican con CFA nivel 3 que estudió en una Universidad de primer nivel, pero sé empático y agradable con tu sentido del humor"
    )

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta.text
                    if delta:
                        yield delta
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""



def stream_income_growth_analysis(
    *,
    symbol: str,
    groups_latest: Dict[str, Dict[str, object]],
    trend: List[Dict[str, object]],
    model: str,
    max_tokens: int = 1500,
    temperature: float = 0.8,
    max_chars: int = 1500,
) -> Generator[str, None, None]:
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    symbol = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    def fmt_pct(v: object) -> str:
        if v is None:
            return "N/D"
        try:
            return f"{float(v) * 100:.2f}%"
        except Exception:
            return str(v)

    # Construye un resumen del latest por categoría, en texto compacto
    latest_lines: List[str] = []
    for cat, fields in (groups_latest or {}).items():
        latest_lines.append(f"{cat}:")
        for k, v in (fields or {}).items():
            latest_lines.append(f"  {k}: {fmt_pct(v) if str(k).startswith('growth') else str(v)}")

    # Tendencia compacta
    trend_lines: List[str] = []
    for row in (trend or [])[:6]:
        d = row.get("date", "N/D")
        fy = row.get("fiscalYear", "N/D")
        per = row.get("period", "N/D")
        trend_lines.append(
            f"{d} FY:{fy} {per}, rev:{fmt_pct(row.get('growthRevenue'))}, gp:{fmt_pct(row.get('growthGrossProfit'))}, "
            f"op:{fmt_pct(row.get('growthOperatingIncome'))}, ebitda:{fmt_pct(row.get('growthEBITDA'))}, "
            f"ni:{fmt_pct(row.get('growthNetIncome'))}, eps:{fmt_pct(row.get('growthEPS'))}"
        )

    user_text = (
        f"Analiza el crecimiento del estado de resultados de {symbol} (Income Statement Growth). "
        "Usa las métricas de crecimiento para evaluar salud operativa, calidad del crecimiento y posibles tensiones. "
        "No inventes comparables ni promedios industriales, limita el análisis a lo provisto.\n\n"
        "Último periodo (agrupado):\n"
        + "\n".join(latest_lines)
        + "\n\nTendencia (últimos periodos):\n"
        + "\n".join(trend_lines)
        + "\n\n"
        "Explica coherencias e inconsistencias, por ejemplo crecimiento de ingresos vs crecimiento de utilidad operativa, "
        "utilidad neta y EPS, además del impacto de impuestos y otros componentes como el costo del financiamiento y el impacto de las tasas de interés."
        "Después de cada explicación técnica reformula la idea para que la entienda cualquier persona con un lenguaje práctico que hasta un niño de 12 años lo pueda entender con ejemplos simples y exageraciones con humor fino y elegante."
        "Sin sonar condescendiente, ni mencionar que tu conclusión es para alguien con entendimiento limitado; evita poner frases como: Conclusión para un niño de doce años. Sé empático y redacta tu conclusión en el último párrafo de forma simple, contundente y fácil de entender"
        f"Limita tu respuesta a {max_chars} caracteres, no uses bullets, tablas, NO títulos o subtítulos, evita emojis y caracteres especiales."
    )

    system = (
        "Asume el rol de un analista financiero CFA con más de 10 años. "
        "Enfócate en crecimiento, calidad de utilidades, apalancamiento operativo y consistencia entre líneas del estado de resultados."
        "Si detectas valores extremos como -1 o 1 en variables de interés o gasto, trátalos como posibles placeholders, outliers o no reportado, "
        "y dilo explícitamente en vez de inventar una interpretación precisa."
    )

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta.text
                    if delta:
                        yield delta
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""

from typing import Dict, List, Generator


# ====================== Operating Profitability ========================

def stream_operating_profitability_growth_analysis(
    *,
    symbol: str,
    operating_group_latest: Dict[str, object],
    trend: List[Dict[str, object]],
    model: str,
    max_tokens: int = 1100,
    temperature: float = 0.55,
    max_chars: int = 1400,
) -> Generator[str, None, None]:
    """
    Análisis de Rentabilidad Operativa basado en Income Statement Growth.
    Compacta grupos B y C, usa trend para coherencia temporal.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    symbol = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    def fmt_pct(v: object) -> str:
        if v is None:
            return "N/D"
        try:
            x = float(v)
            # FMP entrega crecimientos como decimales, ej 0.07 = 7%
            return f"{x * 100:.2f}%"
        except Exception:
            return str(v)

    def is_sentinel(v: object) -> bool:
        try:
            x = float(v)
            return x in (-1.0, 1.0)
        except Exception:
            return False

    latest_lines: List[str] = []
    for k, v in (operating_group_latest or {}).items():
        flag = " (posible sentinel)" if is_sentinel(v) else ""
        latest_lines.append(f"{k}: {fmt_pct(v)}{flag}")

    trend_lines: List[str] = []
    for row in (trend or [])[:8]:
        d = row.get("date", "N/D")
        fy = row.get("fiscalYear", "N/D")
        per = row.get("period", "N/D")
        trend_lines.append(
            f"{d} FY:{fy} {per}, op_exp:{fmt_pct(row.get('growthOperatingExpenses'))}, "
            f"cost_exp:{fmt_pct(row.get('growthCostAndExpenses'))}, rd:{fmt_pct(row.get('growthResearchAndDevelopmentExpenses'))}, "
            f"da:{fmt_pct(row.get('growthDepreciationAndAmortization'))}, op_inc:{fmt_pct(row.get('growthOperatingIncome'))}, "
            f"ebitda:{fmt_pct(row.get('growthEBITDA'))}, ebit:{fmt_pct(row.get('growthEBIT'))}"
        )

    system = (
        "Eres un analista financiero con certificación CFA y más de 10 años de experiencia. "
        "Tu objetivo es evaluar la rentabilidad operativa y la eficiencia de costos a partir de tasas de crecimiento del estado de resultados. "
        "No inventes comparables ni promedios industriales. No inventes causas específicas (productos, mercados, eventos) si no hay evidencia en los datos. "
        "Si detectas valores sentinel como -1 o 1, trátalos como posible dato no reportado, placeholder o outlier, y explica la limitación."
    )

    user = (
        f"Analiza la rentabilidad operativa de {symbol} usando únicamente estos datos de crecimiento TTM/FY (Income Statement Growth). "
        "Enfócate en dos ideas: eficiencia del gasto operativo e impulso de rentabilidad operativa.\n\n"
        "Datos del último periodo (grupo compacto B+C):\n"
        + "\n".join(latest_lines)
        + "\n\nTendencia reciente (para consistencia):\n"
        + "\n".join(trend_lines)
        + "\n\n"
        "Entrega un análisis en español profesional y claro, sin títulos ni subtítulos, sin bullets, sin tablas ni headers introductorios. "
        "No utilices caracteres especiales, evita el uso del signo $ y en cambio usa USD, no uses emojis, ni listas, ni bullets, ni tablas"
        "Después de cada explicación reformula la idea principal para que la pueda entender hasta una persona con la capacidad cognitiva de un joven de 12 años con un ejemplo simple lo pueda entender, Pero sin que el lector se de cuenta que le estás hablando con menor carga cognitiva. Por ejemplo no uses frases como: \"Para un Joven de 12 años\", ni \"Para un niño de 12 años\""
        "Si hay contradicciones (por ejemplo gastos creciendo más rápido que operating income), descríbelas. Si hace falta un dato, no alucines ni inventes información; sugiere qué dato faltaría para confirmar (sin pedirlo al usuario)."
        f"Limita tu respuesta a {max_chars} caracteres."
    )

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta.text
                    if delta:
                        yield delta
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""


def stream_stock_news_summary(
    *,
    symbol: str,
    news_payload: str,
    model: str,
    max_tokens: int = 900,
    temperature: float = 0.3,
    max_chars: int = 1200,
):
    """
    Devuelve chunks (delta) en streaming con un resumen conciso de noticias.
    Formato de salida:
    - Español claro (México)
    - Sin viñetas, sin tablas, sin títulos
    - 1 solo bloque de texto, máximo aprox `max_chars`
    - No inventar, no “completar contexto” externo
    """
    symbol = (symbol or "").strip().upper()
    news_payload = (news_payload or "").strip()
    if not news_payload:
        yield ""
        return

    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    model = (model or get_anthropic_model()).strip()

    system = (
        "Asume el rol de un analista financiero con certificación CFA y más de 10 años de experiencia. "
        "Tu tarea es sintetizar noticias bursátiles con rigor, neutralidad y sin sensacionalismo. "
        "No inventes hechos, si una noticia no trae detalles, dilo con cautela. "
        "Escribe español de México, profesional y claro. "
        "No uses em dashes, no uses viñetas, no uses tablas, no uses títulos."
    )

    user = (
        f"Resume las 20 noticias más recientes relacionadas con {symbol}. "
        "Prioriza lo material para valuación y riesgo (earnings, guidance, regulación, demanda, márgenes, cadena de suministro, litigios). "
        f"Entrega un solo texto corrido, máximo {max_chars} caracteres. "
        "Incluye 1 frase final que indique el sesgo general del flujo de noticias (positivo, mixto, negativo) con cautela. "
        "\n\nFUENTE (NO INVENTAR FUERA DE ESTO):\n"
        f"{news_payload}"
    )

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta.text
                    if delta:
                        yield delta
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""


def stream_grades_actions_analysis(
    *,
    symbol: str,
    insights: dict,
    model: str,
    max_tokens: int = 900,
    temperature: float = 0.5,
):
    """
    Genera interpretación de tendencias de calificaciones usando un payload estructurado.
    Devuelve un stream de texto (deltas), igual que tus otras funciones.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    sym = (symbol or "").strip().upper()

    system_prompt = (
        "Eres un analista financiero profesional. "
        "Tu tarea es interpretar cambios de calificaciones de analistas usando SOLO el payload provisto. "
        "No inventes datos, no asumas eventos fuera de la tabla. "
        "Da señales claras de tendencia, intensidad y posibles cambios de sentimiento. "
        "Si el payload no muestra evidencia suficiente, dilo explícitamente."
    )

    user_prompt = (
        f"Ticker: {sym}\n\n"
        "Tienes un historial reciente de acciones de calificación (últimos eventos). "
        "Analiza tendencias y patrones en:\n"
        "1) Distribución de acciones (upgrade, downgrade, maintain u otras)\n"
        "2) Evidencia de cambios reales de rating (previousGrade != newGrade)\n"
        "3) Concentración por firmas (si una o pocas dominan)\n"
        "4) Cambios recientes relevantes (últimos 5 eventos) y qué sugieren\n\n"
        "Payload:\n"
        f"{insights}\n\n"
        "Entrega el análisis en español, en un tono sobrio, y en 8 a 12 líneas máximo."
    )

    try:
        with client.messages.stream(
            model=model,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            for event in stream:
                delta = getattr(event, "delta", None)
                if delta and getattr(delta, "text", None):
                    yield delta.text
    except Exception:
        yield ""

def stream_sector_peers_dictamen(
    *,
    symbol: str,
    sector: str,
    industry: str,
    peers_limit: int,
    value_quality_table_csv: str,
    roic_table_csv: str,
    stats_text: str,
    model: str,
    max_tokens: int = 1400,
    temperature: float = 0.65,
    max_chars: int = 1400,
) -> Generator[str, None, None]:
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    sym = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    system = (
        "Eres un analista financiero con certificación CFA. "
        "Usas exclusivamente los datos provistos, no inventas, no completas faltantes. "
        "Si falta un dato, lo dices explícitamente. "
        "Español profesional, sin em dashes, sin viñetas, sin títulos, sin Markdown, sin emojis. "
        "No uses el símbolo $, usa USD."
    )

    user = (
        f"Análisis sectorial para {sym}. Sector: {sector}. Industria: {industry}. Set objetivo: {peers_limit}.\n\n"
        f"Estadísticos ROIC del set:\n{stats_text}\n\n"
        "Tabla 1 (Value-Quality, score mayor es mejor):\n"
        f"{value_quality_table_csv}\n\n"
        "Tabla 2 (Ranking ROIC, ROIC mayor es mejor):\n"
        f"{roic_table_csv}\n\n"
        "Formato obligatorio: exactamente 4 párrafos, separados por una línea en blanco, 1 o 2 oraciones por párrafo. "
        "Sin listas. Párrafo 1 diagnóstico del set. Párrafo 2 lectura EV/EBITDA vs ROIC y balance. "
        "Párrafo 3 Top 5 y 2 alternativas con tickers. Párrafo 4 advertencias y una implicación práctica. "
        f"Máximo {max_chars} caracteres."
    )

    emitted = 0
    try:
        with client.messages.stream(
            model=model,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = getattr(event.delta, "text", "")
                    if not delta:
                        continue
                    remaining = max_chars - emitted
                    if remaining <= 0:
                        break
                    if len(delta) > remaining:
                        yield delta[:remaining]
                        break
                    yield delta
                    emitted += len(delta)
                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""


def stream_sector_peers_dictamen(
    *,
    symbol: str,
    sector: str,
    industry: str,
    peers_limit: int,
    value_quality_table_csv: str,
    roic_table_csv: str,
    stats_text: str,
    model: str,
    max_tokens: int = 1400,
    temperature: float = 0.65,
    max_chars: int = 1400,
) -> Generator[str, None, None]:
    """
    Dictamen sectorial en streaming (estricto, sin inventar).
    Entrega texto en español, sobrio, sin em dashes, sin listas, sin títulos.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    sym = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    system = (
        "Eres un analista financiero con certificación CFA, riguroso. "
        "Usas exclusivamente los datos provistos. "
        "Si falta un dato para concluir, lo dices explícitamente. "
        "No inventas comparables, no inventas promedios sectoriales, no asumes causalidad. "
        "Escribes español profesional, sobrio, sin em dashes, sin viñetas, sin títulos, sin Markdown, sin emojis. "
        "No uses el símbolo $, usa USD."
    )

    user = (
        f"Análisis sectorial para {sym}. Sector: {sector}. Industria: {industry}. Set objetivo: {peers_limit}.\n\n"
        "Usa SOLO el contenido siguiente. No inventes.\n\n"
        f"Estadísticos (ROIC):\n{stats_text}\n\n"
        "Tabla 1 (Value-Quality, score mayor es mejor):\n"
        f"{value_quality_table_csv}\n\n"
        "Tabla 2 (Ranking ROIC, ROIC mayor es mejor):\n"
        f"{roic_table_csv}\n\n"
        "Formato obligatorio: exactamente 4 párrafos, separados por una línea en blanco, 1 o 2 oraciones por párrafo. "
        "Sin listas. Párrafo 1 diagnóstico del set. Párrafo 2 lectura EV/EBITDA vs ROIC y balance. "
        "Párrafo 3 Top 5 y 2 alternativas con tickers. Párrafo 4 advertencias y una implicación práctica. "
        f"Máximo {max_chars} caracteres."
    )

    emitted = 0
    try:
        with client.messages.stream(
            model=model,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = getattr(event.delta, "text", "")
                    if not delta:
                        continue

                    remaining = max_chars - emitted
                    if remaining <= 0:
                        break

                    if len(delta) > remaining:
                        yield delta[:remaining]
                        emitted += remaining
                        break

                    yield delta
                    emitted += len(delta)

                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""


def stream_sector_peers_dictamen(
    *,
    symbol: str,
    sector: str,
    industry: str,
    peers_limit: int,
    value_quality_table_csv: str,
    roic_table_csv: str,
    stats_text: str,
    model: str,
    max_tokens: int = 1400,
    temperature: float = 0.65,
    max_chars: int = 1400,
) -> Generator[str, None, None]:
    """
    Dictamen sectorial en streaming, estricto, usa solo datos provistos.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    sym = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    system = (
        "Eres un analista financiero con certificación CFA, riguroso. "
        "Usas exclusivamente los datos provistos. "
        "Si falta un dato, lo dices explícitamente. "
        "No inventas comparables, no inventas promedios sectoriales, no asumes causalidad. "
        "Escribes español profesional, sobrio, sin em dashes, sin viñetas, sin títulos, sin Markdown, sin emojis. "
        "No uses el símbolo $, usa USD."
    )

    user = (
        f"Análisis sectorial para {sym}. Sector: {sector}. Industria: {industry}. Set objetivo: {peers_limit}.\n\n"
        "Usa SOLO el contenido siguiente. No inventes.\n\n"
        f"Estadísticos (ROIC):\n{stats_text}\n\n"
        "Tabla 1 (Value-Quality, score mayor es mejor):\n"
        f"{value_quality_table_csv}\n\n"
        "Tabla 2 (Ranking ROIC, ROIC mayor es mejor):\n"
        f"{roic_table_csv}\n\n"
        "Formato obligatorio: exactamente 4 párrafos, separados por una línea en blanco, 1 o 2 oraciones por párrafo. "
        "Sin listas. Párrafo 1 diagnóstico del set. Párrafo 2 lectura EV/EBITDA vs ROIC y balance. "
        "Párrafo 3 Top 5 y 2 alternativas con tickers. Párrafo 4 advertencias y una implicación práctica. "
        f"Máximo {max_chars} caracteres."
    )

    emitted = 0
    try:
        with client.messages.stream(
            model=model,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = getattr(event.delta, "text", "")
                    if not delta:
                        continue

                    remaining = max_chars - emitted
                    if remaining <= 0:
                        break

                    if len(delta) > remaining:
                        yield delta[:remaining]
                        emitted += remaining
                        break

                    yield delta
                    emitted += len(delta)

                elif getattr(event, "type", "") == "message_stop":
                    break
    except Exception:
        yield ""