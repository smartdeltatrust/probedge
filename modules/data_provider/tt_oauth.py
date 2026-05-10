"""
modules/data_provider/tt_oauth.py
=================================
OAuth2 client para tastytrade (Personal Grant).

Uso:
    from modules.data_provider.tt_oauth import get_oauth_access_token, is_oauth_configured

    if is_oauth_configured():
        bearer = f"Bearer {get_oauth_access_token()}"

El access_token se cachea en memoria con un margen de seguridad antes de
expirar; cuando se vence, automáticamente se intercambia el refresh_token
por uno nuevo. El refresh_token se obtiene una sola vez desde
my.tastytrade.com → Manage → API Access → Manage OAuth Grants.

Variables de entorno requeridas:
    TASTYTRADE_CLIENT_ID
    TASTYTRADE_CLIENT_SECRET
    TASTYTRADE_REFRESH_TOKEN
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TT_API = "https://api.tastytrade.com"
_TOKEN_URL = f"{_TT_API}/oauth/token"
_REFRESH_MARGIN_S = 120  # renovamos 2 min antes de expirar

_cached_access_token: Optional[str] = None
_cached_expires_at: float = 0.0  # epoch seconds


def _load_oauth_env(env_path: Optional[str] = None) -> dict:
    """Lee CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN de os.environ + .env."""
    keys = (
        "TASTYTRADE_CLIENT_ID",
        "TASTYTRADE_CLIENT_SECRET",
        "TASTYTRADE_REFRESH_TOKEN",
    )
    out = {k: os.environ.get(k, "").strip() for k in keys}

    if not all(out.values()):
        if env_path is None:
            env_path = str(Path(__file__).resolve().parents[2] / ".env")
        p = Path(env_path)
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k in keys and not out[k]:
                    out[k] = v
    return out


def is_oauth_configured() -> bool:
    """True si las tres variables OAuth están presentes."""
    creds = _load_oauth_env()
    return all(creds.values())


def _refresh_access_token() -> tuple[str, int]:
    """Llama a POST /oauth/token con grant_type=refresh_token. Retorna (access_token, expires_in)."""
    creds = _load_oauth_env()
    missing = [k for k, v in creds.items() if not v]
    if missing:
        logger.error("oauth refresh_failed reason=missing_env keys=%s", missing)
        raise RuntimeError(f"OAuth incompleto: faltan {missing}")

    body = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": creds["TASTYTRADE_REFRESH_TOKEN"],
        "client_id":     creds["TASTYTRADE_CLIENT_ID"],
        "client_secret": creds["TASTYTRADE_CLIENT_SECRET"],
    }).encode()

    req = urllib.request.Request(
        _TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept":       "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        latency_ms = int((time.time() - t0) * 1000)
        body_preview = e.read().decode()[:300]
        logger.error(
            "oauth refresh_failed http_code=%d latency_ms=%d body=%r",
            e.code, latency_ms, body_preview,
        )
        raise RuntimeError(
            f"OAuth refresh falló (HTTP {e.code}): {body_preview}"
        ) from e
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        logger.error(
            "oauth refresh_failed reason=network_error latency_ms=%d error=%s",
            latency_ms, e,
        )
        raise

    latency_ms = int((time.time() - t0) * 1000)
    access = data.get("access_token", "")
    expires = int(data.get("expires_in", 900))
    if not access:
        logger.error(
            "oauth refresh_failed reason=no_access_token latency_ms=%d response_keys=%s",
            latency_ms, list(data.keys()),
        )
        raise RuntimeError(f"OAuth: respuesta sin access_token: {data}")
    logger.info(
        "oauth refresh_ok latency_ms=%d expires_in_s=%d",
        latency_ms, expires,
    )
    return access, expires


def get_oauth_access_token(force_refresh: bool = False) -> str:
    """Devuelve un access_token válido, renovando si está por expirar."""
    global _cached_access_token, _cached_expires_at
    now = time.time()
    if (
        not force_refresh
        and _cached_access_token
        and now < _cached_expires_at - _REFRESH_MARGIN_S
    ):
        logger.debug(
            "oauth cache_hit expires_in_s=%d",
            int(_cached_expires_at - now),
        )
        return _cached_access_token

    access, expires = _refresh_access_token()
    _cached_access_token = access
    _cached_expires_at = now + expires
    return access


def reset_cache() -> None:
    """Útil para tests o tras revocación manual del grant."""
    global _cached_access_token, _cached_expires_at
    _cached_access_token = None
    _cached_expires_at = 0.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tok = get_oauth_access_token()
    print(f"access_token (truncado): {tok[:24]}...")
    print(f"expira en ~{int(_cached_expires_at - time.time())}s")
