"""
modules/data_provider/dxfeed_quotes.py
=======================================
Quotes en tiempo real vía dxLink WebSocket (tastytrade).

Protocolo dxLink:
  1. SETUP       → handshake inicial
  2. AUTH        → autenticación con token de tastytrade
  3. CHANNEL_REQUEST → abrir canal de feed
  4. FEED_SETUP  → configurar tipo de datos (compact)
  5. FEED_SUBSCRIPTION → suscribir símbolos
  6. Recibir FEED_DATA hasta tener todos los símbolos
  7. CHANNEL_CANCEL + cerrar

Devuelve dict: { "AAPL": {"price": 251.49, "chg_pct": 1.23, "bid": 251.40, "ask": 251.58}, ... }

Uso:
    from modules.data_provider.dxfeed_quotes import get_quotes
    quotes = get_quotes(["AAPL", "CF", "WEC"], tt_token)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constantes del protocolo ──────────────────────────────────────────────────
DXLINK_WS_URL = "wss://tasty-openapi-ws.dxfeed.com/realtime"
TIMEOUT_CONNECT = 10      # segundos para conectar
TIMEOUT_DATA    = 15      # segundos máx esperando que lleguen todos los datos
CHANNEL_ID      = 1       # ID del canal de feed (cualquier entero positivo)

# ── Obtener streamer token de tastytrade ──────────────────────────────────────

def get_streamer_token(tt_token: str) -> tuple[str, str]:
    """
    Retorna (dx_token, ws_url) desde la API de tastytrade.
    tt_token: session token de tastytrade (Authorization header).
    Intenta primero /api-quote-tokens (nuevo), luego /quote-streamer-tokens (legacy).
    """
    for endpoint in [
        "https://api.tastytrade.com/api-quote-tokens",
        "https://api.tastytrade.com/quote-streamer-tokens",
    ]:
        try:
            req = urllib.request.Request(
                endpoint,
                headers={"Authorization": tt_token}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            d = data.get("data", {})
            ws_url = d.get("dxlink-url") or DXLINK_WS_URL
            return d["token"], ws_url
        except Exception as e:
            logger.debug(f"Token endpoint {endpoint} falló: {e}")
            continue
    raise RuntimeError("No se pudo obtener streamer token de tastytrade")


# ── Core async ────────────────────────────────────────────────────────────────

async def _fetch_quotes_async(
    symbols: list[str],
    dx_token: str,
    ws_url: str,
    timeout: float = TIMEOUT_DATA,
) -> dict[str, dict]:
    """
    Conecta al WebSocket dxLink, suscribe símbolos y retorna quotes.
    Evento Trade  → precio last + volumen
    Evento Quote  → bid / ask
    Evento Summary→ prevDayClosePrice para calcular Δ%
    """
    import websockets

    quotes: dict[str, dict] = {s: {} for s in symbols}
    received_trade   = set()
    received_summary = set()
    all_syms         = set(symbols)
    event_fields: dict = {}   # se puebla desde FEED_CONFIG

    # Codificar suscripción en formato dxLink
    trade_subs   = [{"type": "Trade",   "symbol": s} for s in symbols]
    quote_subs   = [{"type": "Quote",   "symbol": s} for s in symbols]
    summary_subs = [{"type": "Summary", "symbol": s} for s in symbols]

    async with websockets.connect(
        ws_url,
        open_timeout=TIMEOUT_CONNECT,
        ping_interval=20,
        ping_timeout=10,
    ) as ws:

        # 1. SETUP
        await ws.send(json.dumps({
            "type": "SETUP",
            "channel": 0,
            "version": "0.1",
            "keepaliveTimeout": 60,
            "acceptKeepaliveTimeout": 60,
        }))

        # 2. AUTH
        await ws.send(json.dumps({
            "type": "AUTH",
            "channel": 0,
            "token": dx_token,
        }))

        # 3. CHANNEL_REQUEST
        await ws.send(json.dumps({
            "type": "CHANNEL_REQUEST",
            "channel": CHANNEL_ID,
            "service": "FEED",
            "parameters": {"contract": "AUTO"},
        }))

        deadline = time.monotonic() + timeout

        async for raw_msg in ws:
            if time.monotonic() > deadline:
                logger.warning("dxFeed timeout — datos parciales recibidos")
                break

            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            mtype   = msg.get("type")
            channel = msg.get("channel", 0)

            # SETUP_ACK — ignorar
            if mtype == "SETUP":
                continue

            # AUTH_STATE — llegan dos mensajes: primero UNAUTHORIZED, luego AUTHORIZED
            if mtype == "AUTH_STATE":
                state = msg.get("state")
                if state == "AUTHORIZED":
                    logger.debug("dxFeed auth OK")
                elif state == "UNAUTHORIZED":
                    # Primer mensaje normal — esperar el siguiente (AUTHORIZED)
                    logger.debug("dxFeed auth pendiente...")
                continue

            # CHANNEL_OPENED → configurar feed y suscribir
            if mtype == "CHANNEL_OPENED" and channel == CHANNEL_ID:
                # FEED_SETUP: datos compactos
                await ws.send(json.dumps({
                    "type": "FEED_SETUP",
                    "channel": CHANNEL_ID,
                    "acceptAggregationPeriod": 0.1,
                    "acceptDataFormat": "COMPACT",
                    "acceptEventFields": {
                        "Trade":   ["eventType", "eventSymbol", "price", "dayVolume", "change"],
                        "Quote":   ["eventType", "eventSymbol", "bidPrice", "askPrice"],
                        "Summary": ["eventType", "eventSymbol", "prevDayClosePrice", "dayOpenPrice"],
                    }
                }))
                # FEED_SUBSCRIPTION
                await ws.send(json.dumps({
                    "type": "FEED_SUBSCRIPTION",
                    "channel": CHANNEL_ID,
                    "reset": True,
                    "add": trade_subs + quote_subs + summary_subs,
                }))
                continue

            # FEED_CONFIG — actualizar campos conocidos
            if mtype == "FEED_CONFIG" and channel == CHANNEL_ID:
                ef = msg.get("eventFields")
                if ef and isinstance(ef, dict):
                    event_fields.update(ef)
                continue

            # FEED_DATA
            if mtype == "FEED_DATA" and channel == CHANNEL_ID:
                data_list = msg.get("data", [])
                _process_feed_data(None, data_list, quotes,
                                   received_trade, received_summary,
                                   event_fields or None)

                # Terminar cuando tengamos Trade + Summary de todos
                if (received_trade >= all_syms and
                        received_summary >= all_syms):
                    logger.debug("Todos los datos recibidos — cerrando")
                    break
                continue

            # KEEPALIVE
            if mtype == "KEEPALIVE":
                await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))
                continue

    # Calcular Δ% final
    for sym, q in quotes.items():
        price = q.get("price") or q.get("bid") or q.get("ask")
        prev  = q.get("prev_close")
        if price and prev and prev != 0:
            q["chg_pct"] = round((price - prev) / prev * 100, 2)
        else:
            q["chg_pct"] = None
        q["price"] = price

    return quotes


def _process_feed_data(
    event_type: str | None,
    data_list: list,
    quotes: dict,
    received_trade: set,
    received_summary: set,
    event_fields: dict | None = None,
) -> None:
    """
    Parsea FEED_DATA en formato dxLink COMPACT.
    
    El formato real observado en tastytrade es:
      data = ["Trade", ["Trade", "AAPL", 251.46, 540896, "Trade", "CF", 120.2, 22709, ...]]
    
    Es decir: data[0] = eventType string, data[1] = flat array de N eventos
    donde cada evento ocupa len(fields) posiciones, con el eventType y symbol incluidos.
    """
    if not data_list or len(data_list) < 2:
        return

    etype = data_list[0]   # "Trade", "Quote", "Summary"
    flat  = data_list[1]   # lista plana con todos los eventos

    if not isinstance(flat, list) or not flat:
        return

    # Los campos esperados según FEED_CONFIG
    fields_map = {
        "Trade":   ["eventType", "eventSymbol", "price", "dayVolume"],
        "Quote":   ["eventType", "eventSymbol", "bidPrice", "askPrice"],
        "Summary": ["eventType", "eventSymbol", "prevDayClosePrice"],
    }
    if event_fields:
        fields_map.update(event_fields)

    fields = fields_map.get(etype)
    if not fields:
        return

    n = len(fields)
    # Iterar en chunks de n
    for i in range(0, len(flat), n):
        chunk = flat[i:i+n]
        if len(chunk) < n:
            break
        obj = dict(zip(fields, chunk))
        obj["eventType"] = etype  # asegurar que el tipo esté en el dict
        _apply_event(obj, quotes, received_trade, received_summary)


def _apply_event(
    obj: dict,
    quotes: dict,
    received_trade: set,
    received_summary: set,
) -> None:
    etype  = obj.get("eventType")
    sym    = obj.get("eventSymbol")
    if not sym or sym not in quotes:
        return

    if etype == "Trade":
        price = obj.get("price")
        if price and price != price:  # NaN check
            return
        if price:
            quotes[sym]["price"]  = float(price)
            quotes[sym]["volume"] = obj.get("dayVolume")
        received_trade.add(sym)

    elif etype == "Quote":
        bid = obj.get("bidPrice")
        ask = obj.get("askPrice")
        if bid: quotes[sym]["bid"] = float(bid)
        if ask: quotes[sym]["ask"] = float(ask)
        # Si no hay Trade, usar mid como precio
        if bid and ask and "price" not in quotes[sym]:
            quotes[sym]["price"] = round((float(bid) + float(ask)) / 2, 2)

    elif etype == "Summary":
        prev = obj.get("prevDayClosePrice")
        day_open = obj.get("dayOpenPrice")
        if prev: quotes[sym]["prev_close"] = float(prev)
        if day_open: quotes[sym]["day_open"] = float(day_open)
        received_summary.add(sym)


# ── API pública ───────────────────────────────────────────────────────────────

def get_quotes(
    symbols: list[str],
    tt_token: str,
    timeout: float = TIMEOUT_DATA,
) -> dict[str, dict]:
    """
    Retorna quotes en tiempo real para una lista de símbolos.

    Args:
        symbols:  Lista de tickers, e.g. ["AAPL", "CF", "WEC"]
        tt_token: Session token de tastytrade
        timeout:  Segundos máx esperando datos (default 15)

    Returns:
        dict con estructura:
        {
            "AAPL": {
                "price":    251.49,   # last trade o mid bid/ask
                "bid":      251.40,
                "ask":      251.58,
                "prev_close": 247.99,
                "chg_pct":  1.41,     # % vs cierre anterior
                "volume":   37465587,
                "day_open": 253.99,
            },
            ...
        }
        Los campos pueden ser None si no llegaron datos.
    """
    dx_token, ws_url = get_streamer_token(tt_token)
    logger.info(f"dxFeed: obteniendo quotes para {len(symbols)} símbolos vía {ws_url}")

    return asyncio.run(
        _fetch_quotes_async(symbols, dx_token, ws_url, timeout)
    )


def get_quotes_from_env(
    symbols: list[str],
    env_path: Optional[str] = None,
    timeout: float = TIMEOUT_DATA,
) -> dict[str, dict]:
    """
    Conveniencia: obtiene token de tastytrade (con auto-renovación) y retorna quotes.
    Reutiliza _get_tt_token() de tastytrade_options para lógica unificada.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from modules.data_provider.tastytrade_options import _get_tt_token
    tt_token = _get_tt_token(env_path)
    return get_quotes(symbols, tt_token, timeout)


# ── CLI rápido para pruebas ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    syms = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "CF", "WEC", "NEM", "LMT"]
    print(f"Obteniendo quotes para: {syms}")

    t0 = time.time()
    results = get_quotes_from_env(syms)
    elapsed = time.time() - t0

    print(f"\n{'SYM':<8} {'PRECIO':>10} {'Δ%':>8}  {'BID':>10} {'ASK':>10}  {'PREV':>10}")
    print("-" * 62)
    for sym in syms:
        q = results.get(sym, {})
        price = q.get("price")
        chg   = q.get("chg_pct")
        bid   = q.get("bid")
        ask   = q.get("ask")
        prev  = q.get("prev_close")

        def fmt(v): return f"${v:>8.2f}" if v else "         —"
        def fmtc(v): return f"{v:>+7.2f}%" if v is not None else "       —"

        print(f"{sym:<8} {fmt(price)} {fmtc(chg)}  {fmt(bid)} {fmt(ask)}  {fmt(prev)}")

    print(f"\n⏱  {elapsed:.2f}s para {len(syms)} símbolos")
