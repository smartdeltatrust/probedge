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
    Returns streaming text deltas (chunks).

    - Summarizes the company description in professional US financial English
    - Single paragraph
    - Up to `max_words` words
    - No em-dashes, no bullets, no headers
    """
    english_text = (english_text or "").strip()
    if not english_text:
        yield ""
        return

    client = get_anthropic_client()
    if client is None:
        # No client — return empty so the UI falls back gracefully.
        yield ""
        return

    sector = (sector or "Unspecified").strip() or "Unspecified"
    model = (model or get_anthropic_model()).strip()

    system = (
        "You are a professional editor for institutional equity research. "
        "Write in US financial English: clear, concise, sober Wall Street tone. "
        "No em-dashes, no bullets, no headers — deliver a single paragraph. "
        "Do not add text before or after the paragraph. "
        "Do not invent information; if a fact is not explicit in the source text, do not assert it."
    )

    user = (
        f"Summarize the following company description in a single paragraph of at most {max_words} words. "
        "Focus on the core product or service. "
        "Mention monetization using the exact phrase: "
        "\"primary revenue source:\". "
        "Avoid founding dates, founders, and claims not present in the source text. "
        f"Close the paragraph by stating the S&P 500 sector: {sector}. "
        "\n\nSOURCE TEXT:\n"
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
        f"Issue a valuation verdict on {symbol} using ONLY the data provided below.\n\n"
        f"Data (USD and multiples):\n"
        f"marketCap: {fmt_usd(multiples.get('marketCap'))} USD\n"
        f"enterpriseValueTTM: {fmt_usd(multiples.get('enterpriseValueTTM'))} USD\n"
        f"evToSalesTTM: {fmt_ratio(multiples.get('evToSalesTTM'))}\n"
        f"evToOperatingCashFlowTTM: {fmt_ratio(multiples.get('evToOperatingCashFlowTTM'))}\n"
        f"evToFreeCashFlowTTM: {fmt_ratio(multiples.get('evToFreeCashFlowTTM'))}\n"
        f"evToEBITDATTM: {fmt_ratio(multiples.get('evToEBITDATTM'))}\n"
        f"grahamNumberTTM: {fmt_usd(multiples.get('grahamNumberTTM'))} USD per share\n"
        f"grahamNetNetTTM: {fmt_usd(multiples.get('grahamNetNetTTM'))}\n\n"
        f"{quote_block}"
        f"{shares_block}"
        "Strict prohibitions: do NOT write headers, do NOT use Markdown (no #, no **, no *), "
        "do NOT use tags like 'Verdict:' or 'Conclusion:'. Do not write any introductory line like 'Valuation by multiples'. "
        "The first letter of your response must be part of the first sentence.\n\n"
        "Required format for mobile: write EXACTLY 4 paragraphs, separated by a blank line (two newlines \\n\\n). "
        "Each paragraph must have 1 or 2 sentences, short but each containing the full main idea.\n\n"
        "Inference rules: do not invent peer comparables or sector averages. If currentPriceUSD is N/A, omit it from the analysis; if present, DO NOT ask for it.\n\n"
        "Content per paragraph (do not copy labels):\n"
        "Paragraph 1: verdict (overvalued or undervalued) and confidence (high, medium or low).\n"
        "Paragraph 2: EV/Sales and EV/EBITDA — what they imply about growth and margins.\n"
        "Paragraph 3: Graham Number — use primaVsNumeroDeGrahamPct (if not N/A) to state premium or discount and contrast with modern intrinsic-value framework.\n"
        "Paragraph 4: closing verdict with one practical implication tied to industry, guidance, or expected growth.\n\n"
        "Style: sober, professional US financial English. Wall Street institutional tone. No cultural references, no humor, no idioms. "
        "Concise, disciplined, data-driven.\n\n"
        f"Final constraints: max {max_chars} characters. No emojis or special characters. Do not use the $ symbol — write USD."
    )

    system = (
        "You are a CFA-charterholder equity analyst with 10+ years of experience valuing public companies. "
        "You prioritize clarity, data discipline, and logical consistency. "
        "You use only the information provided; you do not invent figures or industry ranges. "
        "If currentPriceUSD is provided, you use it to interpret the Graham Number and you do not request it. "
        "If shares-float data is provided (outstandingShares, floatShares, freeFloat), you interpret it qualitatively "
        "without inventing industry thresholds or comparables. "
        "You write in professional US financial English, plain text, paragraphs only, no headers, no Markdown."
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
        f"Analyze profitability and returns for {symbol} using the following TTM metrics:\n\n"
        f"returnOnAssetsTTM: {fmt_pct(returns.get('returnOnAssetsTTM'))}\n"
        f"returnOnEquityTTM: {fmt_pct(returns.get('returnOnEquityTTM'))}\n"
        f"returnOnTangibleAssetsTTM: {fmt_pct(returns.get('returnOnTangibleAssetsTTM'))}\n"
        f"returnOnInvestedCapitalTTM: {fmt_pct(returns.get('returnOnInvestedCapitalTTM'))}\n"
        f"returnOnCapitalEmployedTTM: {fmt_pct(returns.get('returnOnCapitalEmployedTTM'))}\n"
        f"operatingReturnOnAssetsTTM: {fmt_pct(returns.get('operatingReturnOnAssetsTTM'))}\n"
        f"earningsYieldTTM: {fmt_pct(returns.get('earningsYieldTTM'))}\n"
        f"freeCashFlowYieldTTM: {fmt_pct(returns.get('freeCashFlowYieldTTM'))}\n\n"
        "Explain what these returns reveal about operating efficiency, reinvestment capacity, and quality of return. "
        "Flag consistencies, red flags, or green flags — for example, ROE materially above ROA — and explain what that may imply "
        "in terms of leverage or capital base, using only the data provided. Briefly clarify each acronym on first use "
        "(e.g., ROA = Return on Assets).\n\n"
        "Tone: sober, professional US financial English. Wall Street institutional voice. "
        "No humor, no cultural references, no idioms, no condescension. "
        "Close with a concise, decisive paragraph that synthesizes the picture.\n\n"
        f"Constraints: max {max_chars} characters. No bullets, tables, headers, or subheaders. No emojis or special characters."
    )

    system = (
        "You are a CFA-charterholder equity analyst with 10+ years of experience. "
        "Write in clear, professional US financial English. "
        "Connect related metrics (ROA vs ROE, ROIC/ROCE, operating ROA, yields). "
        "Do not invent peer comparables or industry averages if they are not provided. Use only the data presented. "
        "If a metric is extreme, explain why it may occur and what additional data would be needed to confirm. "
        "Tone is sober institutional Wall Street: no cultural references, no humor, no idioms."
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
        f"Analyze the income statement growth profile of {symbol}. "
        "Use the growth metrics to assess operating health, quality of growth, and possible stress points. "
        "Do not invent peer comparables or industry averages; limit the analysis strictly to the data provided.\n\n"
        "Latest period (grouped):\n"
        + "\n".join(latest_lines)
        + "\n\nTrend (recent periods):\n"
        + "\n".join(trend_lines)
        + "\n\n"
        "Explain consistencies and inconsistencies — for example, revenue growth vs operating income growth, "
        "net income, EPS, and the impact of taxes and other line items such as financing costs and interest rate sensitivity.\n\n"
        "Tone: sober, professional US financial English. Institutional Wall Street voice. "
        "No humor, no idioms, no cultural references. Close with a concise, decisive synthesis paragraph.\n\n"
        f"Constraints: max {max_chars} characters. No bullets, tables, headers, or subheaders. No emojis or special characters."
    )

    system = (
        "You are a CFA-charterholder equity analyst with 10+ years of experience. "
        "Focus on growth, quality of earnings, operating leverage, and consistency across income statement lines. "
        "If you detect extreme sentinel values such as -1 or 1 in interest or expense fields, treat them as possible placeholders, "
        "outliers, or not-reported, and say so explicitly rather than invent a precise interpretation. "
        "Write in professional US financial English."
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
        flag = " (possible sentinel)" if is_sentinel(v) else ""
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
        "You are a CFA-charterholder equity analyst with 10+ years of experience. "
        "Your objective is to assess operating profitability and cost efficiency from income-statement growth rates. "
        "Do not invent peer comparables or industry averages. Do not invent specific causes (products, markets, events) absent direct evidence in the data. "
        "If you detect sentinel values such as -1 or 1, treat them as potentially not-reported, placeholder, or outlier values, and state the limitation explicitly. "
        "Write in professional US financial English, institutional Wall Street tone."
    )

    user = (
        f"Analyze operating profitability for {symbol} using ONLY these TTM/FY growth metrics (Income Statement Growth). "
        "Focus on two ideas: operating-expense efficiency and operating-profitability momentum.\n\n"
        "Latest period (compact B+C group):\n"
        + "\n".join(latest_lines)
        + "\n\nRecent trend (for consistency check):\n"
        + "\n".join(trend_lines)
        + "\n\n"
        "Deliver the analysis in clear, professional US financial English. No headers, no subheaders, no bullets, no tables, no introductory headers. "
        "Do not use special characters. Avoid the $ symbol — use USD. No emojis, lists, bullets, or tables. "
        "Tone: sober institutional Wall Street voice. No humor, no idioms, no cultural references. "
        "If you find contradictions (e.g., expenses growing faster than operating income), describe them. "
        "If data is missing, do not hallucinate; suggest what additional data would be needed for confirmation (without requesting it from the user). "
        f"Constraint: maximum {max_chars} characters."
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
    Returns streaming text deltas with a concise news summary.
    Output format:
    - Professional US financial English
    - No bullets, no tables, no headers
    - Single continuous text block, up to ~max_chars
    - No invention, no external context-filling
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
        "You are a CFA-charterholder equity analyst with 10+ years of experience. "
        "Your task is to synthesize equity news with rigor, neutrality, and no sensationalism. "
        "Do not invent facts. If a headline lacks detail, say so with caution. "
        "Write in professional US financial English. Institutional Wall Street tone. "
        "No em-dashes, no bullets, no tables, no headers."
    )

    user = (
        f"Summarize the most recent news items related to {symbol}. "
        "Prioritize what is material to valuation and risk (earnings, guidance, regulation, demand, margins, supply chain, litigation). "
        f"Deliver a single continuous paragraph, max {max_chars} characters. "
        "End with one cautious sentence stating the overall tilt of the news flow (positive, mixed, negative). "
        "\n\nSOURCE (DO NOT GO BEYOND THIS):\n"
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
    Interprets rating-action trends using a structured payload.
    Returns a streaming text generator, same as the other functions.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    sym = (symbol or "").strip().upper()

    system_prompt = (
        "You are a professional equity analyst. "
        "Your task is to interpret analyst rating actions using ONLY the payload provided. "
        "Do not invent data; do not assume events outside the table. "
        "Provide clear signals on trend, intensity, and potential shifts in sentiment. "
        "If the payload does not show sufficient evidence, say so explicitly. "
        "Write in professional US financial English."
    )

    user_prompt = (
        f"Ticker: {sym}\n\n"
        "You have a recent history of rating actions (latest events). "
        "Analyze trends and patterns across:\n"
        "1) Distribution of actions (upgrade, downgrade, maintain, other)\n"
        "2) Evidence of real rating changes (previousGrade != newGrade)\n"
        "3) Firm concentration (whether one or few firms dominate)\n"
        "4) Recent relevant changes (last 5 events) and what they suggest\n\n"
        "Payload:\n"
        f"{insights}\n\n"
        "Deliver the analysis in US financial English, sober institutional tone, 8 to 12 lines maximum."
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
        "You are a CFA-charterholder equity analyst. "
        "You use exclusively the data provided; you do not invent or fill in missing values. "
        "If data is missing, you state so explicitly. "
        "Write in professional US financial English, sober institutional Wall Street tone. "
        "No em-dashes, no bullets, no headers, no Markdown, no emojis. "
        "Do not use the $ symbol — write USD."
    )

    user = (
        f"Sector analysis for {sym}. Sector: {sector}. Industry: {industry}. Target peer set: {peers_limit}.\n\n"
        f"Peer ROIC statistics:\n{stats_text}\n\n"
        "Table 1 (Value-Quality, higher score = better):\n"
        f"{value_quality_table_csv}\n\n"
        "Table 2 (ROIC Ranking, higher ROIC = better):\n"
        f"{roic_table_csv}\n\n"
        "Required format: exactly 4 paragraphs, separated by a blank line, 1 or 2 sentences per paragraph. "
        "No lists. Paragraph 1: diagnostic of the peer set. Paragraph 2: read EV/EBITDA vs ROIC balance. "
        "Paragraph 3: Top 5 names and 2 alternatives with tickers. Paragraph 4: caveats and one practical implication. "
        f"Maximum {max_chars} characters."
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
    Streaming sector verdict (strict, no invention).
    Delivers professional US financial English text, sober institutional tone,
    no em-dashes, no lists, no headers.
    """
    client = get_anthropic_client()
    if client is None:
        yield ""
        return

    sym = (symbol or "").strip().upper()
    model = (model or get_anthropic_model()).strip()

    system = (
        "You are a rigorous CFA-charterholder equity analyst. "
        "You use exclusively the data provided. "
        "If data is missing to conclude, you say so explicitly. "
        "You do not invent peer comparables, you do not invent sector averages, you do not assume causality. "
        "Write in professional US financial English, sober institutional Wall Street tone. "
        "No em-dashes, no bullets, no headers, no Markdown, no emojis. "
        "Do not use the $ symbol — write USD."
    )

    user = (
        f"Sector analysis for {sym}. Sector: {sector}. Industry: {industry}. Target peer set: {peers_limit}.\n\n"
        "Use ONLY the content below. Do not invent.\n\n"
        f"ROIC statistics:\n{stats_text}\n\n"
        "Table 1 (Value-Quality, higher score = better):\n"
        f"{value_quality_table_csv}\n\n"
        "Table 2 (ROIC Ranking, higher ROIC = better):\n"
        f"{roic_table_csv}\n\n"
        "Required format: exactly 4 paragraphs, separated by a blank line, 1 or 2 sentences per paragraph. "
        "No lists. Paragraph 1: diagnostic of the peer set. Paragraph 2: read EV/EBITDA vs ROIC balance. "
        "Paragraph 3: Top 5 names and 2 alternatives with tickers. Paragraph 4: caveats and one practical implication. "
        f"Maximum {max_chars} characters."
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
        "You are a rigorous CFA-charterholder equity analyst. "
        "You use exclusively the data provided. "
        "If data is missing, you state so explicitly. "
        "You do not invent peer comparables, you do not invent sector averages, you do not assume causality. "
        "Write in professional US financial English, sober institutional Wall Street tone. "
        "No em-dashes, no bullets, no headers, no Markdown, no emojis. "
        "Do not use the $ symbol — write USD."
    )

    user = (
        f"Sector analysis for {sym}. Sector: {sector}. Industry: {industry}. Target peer set: {peers_limit}.\n\n"
        "Use ONLY the content below. Do not invent.\n\n"
        f"ROIC statistics:\n{stats_text}\n\n"
        "Table 1 (Value-Quality, higher score = better):\n"
        f"{value_quality_table_csv}\n\n"
        "Table 2 (ROIC Ranking, higher ROIC = better):\n"
        f"{roic_table_csv}\n\n"
        "Required format: exactly 4 paragraphs, separated by a blank line, 1 or 2 sentences per paragraph. "
        "No lists. Paragraph 1: diagnostic of the peer set. Paragraph 2: read EV/EBITDA vs ROIC balance. "
        "Paragraph 3: Top 5 names and 2 alternatives with tickers. Paragraph 4: caveats and one practical implication. "
        f"Maximum {max_chars} characters."
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