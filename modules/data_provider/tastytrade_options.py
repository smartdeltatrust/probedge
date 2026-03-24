"""
modules/data_provider/tastytrade_options.py
============================================
Proveedor de cadenas de opciones y precio spot vía tastytrade + dxFeed WebSocket.
Reemplaza modules/data_provider/massive.py (Polygon).

Funciones públicas (misma firma que massive.py):
    fetch_available_expiries(ticker, tt_token) -> list[str]
    fetch_options_snapshot(ticker, expiry, tt_token) -> pd.DataFrame
    get_spot_price(ticker, tt_token) -> float

Uso CLI:
    python3 -m modules.data_provider.tastytrade_options AAPL 2026-04-17
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

from modules.data_provider.dxfeed_quotes import get_quotes, get_streamer_token

logger = logging.getLogger(__name__)

_TT_API = "https://api.tastytrade.com"
TIMEOUT_DATA = 30  # Segundos — más amplio porque hay muchos símbolos


# ── Token helper ──────────────────────────────────────────────────────────────

def _load_credentials(env_path: Optional[str] = None) -> dict:
    """Carga credenciales desde os.environ primero, luego archivo .env."""
    import os
    creds = {
        "login":          os.environ.get("TASTYTRADE_LOGIN", ""),
        "password":       os.environ.get("TASTYTRADE_PASSWORD", ""),
        "remember_token": os.environ.get("TASTYTRADE_REMEMBER_TOKEN", ""),
    }
    # Completar con .env si faltan valores
    if not all(creds.values()):
        if env_path is None:
            env_path = str(Path.home() / "projects/Risk-Neutral-Density-Probabilities/.env")
        if Path(env_path).exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip()
                        if k == "TASTYTRADE_LOGIN"          and not creds["login"]:
                            creds["login"] = v
                        elif k == "TASTYTRADE_PASSWORD"     and not creds["password"]:
                            creds["password"] = v
                        elif k == "TASTYTRADE_REMEMBER_TOKEN" and not creds["remember_token"]:
                            creds["remember_token"] = v
    return creds


def _session_valid(token: str) -> bool:
    """Verifica que el session token sigue activo."""
    req = urllib.request.Request(
        f"{_TT_API}/customers/me",
        headers={"Authorization": token}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def _auth_with_remember(login: str, remember: str) -> Optional[dict]:
    """Intenta autenticar con remember token. Retorna data dict o None."""
    payload = json.dumps({"login": login, "remember-token": remember, "remember-me": True}).encode()
    req = urllib.request.Request(
        f"{_TT_API}/sessions", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("data", {})
    except Exception:
        return None


def _auth_with_password(login: str, password: str) -> Optional[dict]:
    """Intenta autenticar con password (sin OTP — funciona desde IPs conocidas)."""
    payload = json.dumps({"login": login, "password": password, "remember-me": True}).encode()
    req = urllib.request.Request(
        f"{_TT_API}/sessions", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("data", {})
    except Exception:
        return None


def _save_session(token_file: Path, session_token: str, remember_token: str = "") -> None:
    """Guarda session token en cache y actualiza .env si hay nuevo remember token."""
    token_file.write_text(session_token)
    if remember_token:
        env_path = Path.home() / "projects/Risk-Neutral-Density-Probabilities/.env"
        if env_path.exists():
            lines = env_path.read_text().splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("TASTYTRADE_REMEMBER_TOKEN="):
                    new_lines.append(f"TASTYTRADE_REMEMBER_TOKEN={remember_token}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"TASTYTRADE_REMEMBER_TOKEN={remember_token}")
            env_path.write_text("\n".join(new_lines) + "\n")
            logger.info("Remember token actualizado en .env")


def _get_tt_token(env_path: Optional[str] = None) -> str:
    """
    Obtiene session token de tastytrade con auto-renovación.

    Orden de prioridad:
    1. /tmp/tt_token.txt — cache local (validado contra API)
    2. TASTYTRADE_REMEMBER_TOKEN — renovación sin OTP
    3. TASTYTRADE_PASSWORD — login directo (funciona sin OTP desde IPs conocidas)

    Al renovar: guarda el nuevo session token en /tmp y actualiza
    el remember token en .env para el siguiente arranque.
    """
    token_file = Path("/tmp/tt_token.txt")

    # 1. Cache — verificar que sigue válido
    if token_file.exists():
        cached = token_file.read_text().strip()
        if cached and _session_valid(cached):
            return cached
        logger.info("Cache expirado — renovando token")

    creds = _load_credentials(env_path)
    login = creds["login"]
    if not login:
        raise RuntimeError("TASTYTRADE_LOGIN no configurado")

    # 2. Password directo — más confiable en producción (no expira como el remember token)
    if creds["password"]:
        data = _auth_with_password(login, creds["password"])
        if data and data.get("session-token"):
            logger.info("Token renovado via password")
            _save_session(token_file, data["session-token"], data.get("remember-token", ""))
            return data["session-token"]
        logger.warning("Password falló — intentando remember token")

    # 3. Remember token como fallback
    if creds["remember_token"]:
        data = _auth_with_remember(login, creds["remember_token"])
        if data and data.get("session-token"):
            logger.info("Token renovado via remember-token")
            _save_session(token_file, data["session-token"], data.get("remember-token", ""))
            return data["session-token"]

    raise RuntimeError(
        "No se pudo autenticar con tastytrade. "
        "Verifica TASTYTRADE_LOGIN y TASTYTRADE_PASSWORD en Render."
    )


# ── Vencimientos ──────────────────────────────────────────────────────────────

def fetch_available_expiries(ticker: str, tt_token: str) -> list[str]:
    """
    Retorna lista de fechas 'YYYY-MM-DD' disponibles para el ticker.

    Usa GET /option-chains/{ticker}/nested de tastytrade.
    """
    url = f"{_TT_API}/option-chains/{ticker.upper()}/nested"
    req = urllib.request.Request(url, headers={"Authorization": tt_token})

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise RuntimeError(f"Error obteniendo cadena nested para {ticker}: {e}") from e

    items = data.get("data", {}).get("items", [])
    if not items:
        return []

    expirations = items[0].get("expirations", [])
    dates = [exp["expiration-date"] for exp in expirations if "expiration-date" in exp]
    return sorted(set(dates))


# ── Snapshot de opciones vía dxFeed ──────────────────────────────────────────

def _get_nested_strikes(ticker: str, expiry: str, tt_token: str) -> list[dict]:
    """
    Llama a /option-chains/{ticker}/nested y extrae strikes del expiry dado.
    Retorna lista de dicts con:
        strike, call_sym, put_sym
    """
    url = f"{_TT_API}/option-chains/{ticker.upper()}/nested"
    req = urllib.request.Request(url, headers={"Authorization": tt_token})

    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())

    items = data.get("data", {}).get("items", [])
    if not items:
        return []

    target_exp = None
    for exp in items[0].get("expirations", []):
        if exp.get("expiration-date") == expiry:
            target_exp = exp
            break

    if not target_exp:
        raise ValueError(f"Vencimiento {expiry} no encontrado para {ticker}")

    results = []
    for s in target_exp.get("strikes", []):
        strike = s.get("strike-price")
        call_sym = s.get("call-streamer-symbol", "")
        put_sym  = s.get("put-streamer-symbol", "")
        if strike and (call_sym or put_sym):
            results.append({
                "strike": float(strike),
                "call_sym": call_sym,
                "put_sym": put_sym,
            })

    return results


async def _fetch_options_async(
    symbols: list[str],
    dx_token: str,
    ws_url: str,
    timeout: float = TIMEOUT_DATA,
) -> dict[str, dict]:
    """
    Conecta al WebSocket dxLink, suscribe símbolos de opciones y retorna
    Greeks + Quote para cada uno.

    Retorna dict: { ".AAPL260417C250": {"bid": x, "ask": x, "iv": x, "delta": x, ...} }
    """
    import websockets

    CHANNEL_ID = 1

    results: dict[str, dict] = {s: {} for s in symbols}
    received_greeks = set()
    received_quote  = set()
    all_syms = set(symbols)

    # Suscripciones para Greeks y Quote
    greeks_subs = [{"type": "Greeks", "symbol": s} for s in symbols]
    quote_subs  = [{"type": "Quote",  "symbol": s} for s in symbols]

    greeks_fields = ["eventType", "eventSymbol", "volatility", "delta",
                     "gamma", "theta", "vega", "rho"]
    quote_fields  = ["eventType", "eventSymbol", "bidPrice", "askPrice"]

    async with websockets.connect(
        ws_url,
        open_timeout=10,
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
        event_fields: dict = {}

        async for raw_msg in ws:
            if time.monotonic() > deadline:
                logger.warning(
                    f"dxFeed timeout — Greeks: {len(received_greeks)}/{len(all_syms)}, "
                    f"Quote: {len(received_quote)}/{len(all_syms)}"
                )
                break

            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            mtype   = msg.get("type")
            channel = msg.get("channel", 0)

            if mtype in ("SETUP", "ERROR"):
                continue

            if mtype == "AUTH_STATE":
                if msg.get("state") == "AUTHORIZED":
                    logger.debug("dxFeed auth OK (options)")
                continue

            if mtype == "CHANNEL_OPENED" and channel == CHANNEL_ID:
                # FEED_SETUP con Greeks + Quote
                await ws.send(json.dumps({
                    "type": "FEED_SETUP",
                    "channel": CHANNEL_ID,
                    "acceptAggregationPeriod": 0.1,
                    "acceptDataFormat": "COMPACT",
                    "acceptEventFields": {
                        "Greeks": greeks_fields,
                        "Quote":  quote_fields,
                    },
                }))
                # Suscribir en lotes de 500 para no exceder límites
                batch_size = 500
                all_subs = greeks_subs + quote_subs
                first_batch = True
                for i in range(0, len(all_subs), batch_size):
                    batch = all_subs[i:i + batch_size]
                    await ws.send(json.dumps({
                        "type": "FEED_SUBSCRIPTION",
                        "channel": CHANNEL_ID,
                        "reset": first_batch,
                        "add": batch,
                    }))
                    first_batch = False
                continue

            if mtype == "FEED_CONFIG" and channel == CHANNEL_ID:
                ef = msg.get("eventFields")
                if ef and isinstance(ef, dict):
                    event_fields.update(ef)
                continue

            if mtype == "FEED_DATA" and channel == CHANNEL_ID:
                data_list = msg.get("data", [])
                _process_options_feed_data(
                    data_list, results, received_greeks, received_quote,
                    event_fields, greeks_fields, quote_fields
                )

                # Terminar cuando tengamos Greeks + Quote de todos (o suficiente)
                if (received_greeks >= all_syms and received_quote >= all_syms):
                    logger.debug("Todos los datos de opciones recibidos")
                    break
                continue

            if mtype == "KEEPALIVE":
                await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))
                continue

    return results


def _process_options_feed_data(
    data_list: list,
    results: dict,
    received_greeks: set,
    received_quote: set,
    event_fields: dict,
    default_greeks_fields: list,
    default_quote_fields: list,
) -> None:
    """
    Parsea FEED_DATA en formato COMPACT para Greeks y Quote.
    data_list[0] = eventType string (ej. "Greeks")
    data_list[1] = flat array de eventos
    """
    if not data_list or len(data_list) < 2:
        return

    etype = data_list[0]
    flat  = data_list[1]

    if not isinstance(flat, list) or not flat:
        return

    # Usar campos del FEED_CONFIG si disponibles, si no usar defaults
    if etype == "Greeks":
        fields = event_fields.get("Greeks", default_greeks_fields)
    elif etype == "Quote":
        fields = event_fields.get("Quote", default_quote_fields)
    else:
        return

    n = len(fields)
    for i in range(0, len(flat), n):
        chunk = flat[i:i + n]
        if len(chunk) < n:
            break
        obj = dict(zip(fields, chunk))
        sym = obj.get("eventSymbol")
        if not sym or sym not in results:
            continue

        if etype == "Greeks":
            iv    = obj.get("volatility")
            delta = obj.get("delta")
            gamma = obj.get("gamma")
            theta = obj.get("theta")
            vega  = obj.get("vega")
            rho   = obj.get("rho")
            if iv    is not None: results[sym]["iv"]    = float(iv)
            if delta is not None: results[sym]["delta"] = float(delta)
            if gamma is not None: results[sym]["gamma"] = float(gamma)
            if theta is not None: results[sym]["theta"] = float(theta)
            if vega  is not None: results[sym]["vega"]  = float(vega)
            if rho   is not None: results[sym]["rho"]   = float(rho)
            received_greeks.add(sym)

        elif etype == "Quote":
            bid = obj.get("bidPrice")
            ask = obj.get("askPrice")
            if bid is not None: results[sym]["bid"] = float(bid)
            if ask is not None: results[sym]["ask"] = float(ask)
            received_quote.add(sym)


# ── API pública ───────────────────────────────────────────────────────────────

def fetch_options_snapshot(ticker: str, expiry: str, tt_token: str) -> pd.DataFrame:
    """
    Retorna DataFrame con columnas:
        strike, contract_type (C/P), bid, ask, last_price,
        open_interest, iv, delta, gamma, theta, vega, volume

    Flujo:
    1. GET /option-chains/{ticker}/nested → obtener streamer symbols del expiry
    2. Suscribir todos los streamer symbols a dxFeed (Greeks + Quote)
    3. Construir DataFrame con los datos recibidos

    Nota: open_interest, last_price y volume NO están disponibles vía dxFeed
    en tiempo real (requieren endpoint REST adicional de tastytrade). Se dejan
    como NaN — el módulo de RND solo requiere bid/ask/iv/delta/gamma.
    """
    logger.info(f"Obteniendo strikes de {ticker} exp {expiry}...")
    strikes = _get_nested_strikes(ticker, expiry, tt_token)

    if not strikes:
        logger.warning(f"No hay strikes para {ticker} {expiry}")
        return pd.DataFrame()

    # Construir mapa: streamer_sym -> (strike, contract_type)
    sym_meta: dict[str, tuple[float, str]] = {}
    for s in strikes:
        if s["call_sym"]:
            sym_meta[s["call_sym"]] = (s["strike"], "C")
        if s["put_sym"]:
            sym_meta[s["put_sym"]] = (s["strike"], "P")

    all_syms = list(sym_meta.keys())
    logger.info(f"Suscribiendo {len(all_syms)} símbolos de opciones a dxFeed...")

    dx_token, ws_url = get_streamer_token(tt_token)
    dxfeed_data = asyncio.run(
        _fetch_options_async(all_syms, dx_token, ws_url, timeout=TIMEOUT_DATA)
    )

    rows = []
    for sym, data in dxfeed_data.items():
        if sym not in sym_meta:
            continue
        strike, contract_type = sym_meta[sym]
        row = {
            "strike":        strike,
            "contract_type": contract_type,
            "bid":           data.get("bid"),
            "ask":           data.get("ask"),
            "last_price":    None,       # No disponible vía dxFeed streaming
            "open_interest": None,       # No disponible vía dxFeed streaming
            "iv":            data.get("iv"),
            "delta":         data.get("delta"),
            "gamma":         data.get("gamma"),
            "theta":         data.get("theta"),
            "vega":          data.get("vega"),
            "volume":        None,       # No disponible vía dxFeed streaming
            "rho":           data.get("rho"),
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Convertir numéricos
    num_cols = ["strike", "bid", "ask", "iv", "delta", "gamma", "theta", "vega", "rho"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Ordenar por strike y tipo de contrato
    df = df.sort_values(["strike", "contract_type"]).reset_index(drop=True)

    snapshot_cols = [
        "strike", "contract_type", "bid", "ask", "last_price",
        "open_interest", "iv", "delta", "gamma", "theta", "vega", "volume",
    ]
    available = [c for c in snapshot_cols if c in df.columns]
    return df[available]


def get_spot_price(ticker: str, tt_token: str) -> float:
    """
    Precio spot vía dxFeed (reusar dxfeed_quotes.get_quotes).
    Retorna el último precio del subyacente.
    """
    quotes = get_quotes([ticker.upper()], tt_token)
    q = quotes.get(ticker.upper(), {})
    price = q.get("price") or q.get("bid") or q.get("ask")
    if not price:
        raise ValueError(f"No se pudo obtener precio spot para {ticker} vía dxFeed")
    return float(price)


# ── CLI para validación ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    expiry = sys.argv[2] if len(sys.argv) > 2 else "2026-04-17"

    print(f"\n{'='*60}")
    print(f"tastytrade_options — {ticker} exp {expiry}")
    print(f"{'='*60}")

    tt_token = _get_tt_token()

    # 1. Vencimientos disponibles
    print(f"\n📅 Vencimientos disponibles para {ticker}:")
    t0 = time.time()
    expiries = fetch_available_expiries(ticker, tt_token)
    print(f"   {len(expiries)} vencimientos en {time.time()-t0:.1f}s")
    print(f"   Primeros 5: {expiries[:5]}")

    # 2. Precio spot
    print(f"\n💵 Precio spot de {ticker}:")
    t0 = time.time()
    try:
        spot = get_spot_price(ticker, tt_token)
        print(f"   ${spot:.2f} ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"   ERROR: {e}")
        spot = None

    # 3. Cadena de opciones
    print(f"\n📊 Cadena de opciones {ticker} {expiry}:")
    t0 = time.time()
    df = fetch_options_snapshot(ticker, expiry, tt_token)
    elapsed = time.time() - t0

    if df.empty:
        print("   Sin datos")
    else:
        print(f"   {len(df)} contratos en {elapsed:.1f}s")
        print(f"\n{'STRIKE':>8} {'TYPE':>5} {'BID':>8} {'ASK':>8} {'IV':>8} {'DELTA':>8} {'GAMMA':>8}")
        print("-" * 62)
        # Mostrar algunos strikes ATM
        if spot:
            atm_idx = (df["strike"] - spot).abs().idxmin()
            show_idx = range(max(0, atm_idx - 5), min(len(df), atm_idx + 6))
        else:
            show_idx = range(min(11, len(df)))

        for i in show_idx:
            row = df.iloc[i]
            def f(v): return f"{v:>8.4f}" if pd.notna(v) else "       —"
            print(
                f"{row.strike:>8.2f} {row.contract_type:>5} "
                f"{f(row.bid)} {f(row.ask)} {f(row.iv)} {f(row.delta)} {f(row.gamma)}"
            )

        print(f"\n📈 Stats:")
        for col in ["bid", "ask", "iv", "delta", "gamma"]:
            if col in df.columns:
                non_null = df[col].dropna()
                print(f"   {col}: {len(non_null)}/{len(df)} no-nulos", end="")
                if len(non_null) > 0:
                    print(f" | rango [{non_null.min():.4f}, {non_null.max():.4f}]")
                else:
                    print()
