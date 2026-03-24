#!/usr/bin/env python3
"""
scripts/renew_tt_token.py
==========================
Renueva el session token de tastytrade y actualiza:
  1. /tmp/tt_token.txt         (cache local)
  2. .env del proyecto          (remember token)
  3. Render env vars            (si RENDER_API_KEY está configurada)

Diseñado para correr como cron diario desde la máquina local (donde
existe la sesión activa). Render no puede hacer esto solo porque no
tiene acceso al TOTP — pero login+password funciona sin OTP desde
IPs conocidas.

Uso:
    python3 scripts/renew_tt_token.py
    python3 scripts/renew_tt_token.py --notify-whatsapp   # alerta si falla
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TOKEN_FILE = Path("/tmp/tt_token.txt")
TT_API = "https://api.tastytrade.com"


def load_env() -> dict:
    env = {}
    # os.environ primero
    for k in ["TASTYTRADE_LOGIN", "TASTYTRADE_PASSWORD", "TASTYTRADE_REMEMBER_TOKEN", "RENDER_API_KEY"]:
        v = os.environ.get(k, "")
        if v:
            env[k] = v
    # .env como complemento
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if k not in env:
                        env[k] = v
    return env


def renew_token(env: dict) -> tuple[str, str]:
    """
    Renueva el token. Intenta remember token primero, luego password.
    Retorna (session_token, new_remember_token).
    """
    login    = env.get("TASTYTRADE_LOGIN", "")
    password = env.get("TASTYTRADE_PASSWORD", "")
    remember = env.get("TASTYTRADE_REMEMBER_TOKEN", "")

    if not login:
        raise RuntimeError("TASTYTRADE_LOGIN no configurado")

    # Intento 1: remember token
    if remember:
        payload = json.dumps({"login": login, "remember-token": remember, "remember-me": True}).encode()
        req = urllib.request.Request(
            f"{TT_API}/sessions", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read()).get("data", {})
            if data.get("session-token"):
                logger.info("✅ Token renovado via remember-token")
                return data["session-token"], data.get("remember-token", remember)
        except Exception as e:
            logger.warning(f"Remember token falló: {e} — intentando password")

    # Intento 2: password
    if password:
        payload = json.dumps({"login": login, "password": password, "remember-me": True}).encode()
        req = urllib.request.Request(
            f"{TT_API}/sessions", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read()).get("data", {})
        if data.get("session-token"):
            logger.info("✅ Token renovado via password")
            return data["session-token"], data.get("remember-token", "")

    raise RuntimeError("No se pudo renovar el token — verifica credenciales")


def update_env_file(remember_token: str) -> None:
    if not ENV_PATH.exists():
        return
    lines = ENV_PATH.read_text().splitlines()
    new_lines, found = [], False
    for line in lines:
        if line.startswith("TASTYTRADE_REMEMBER_TOKEN="):
            new_lines.append(f"TASTYTRADE_REMEMBER_TOKEN={remember_token}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"TASTYTRADE_REMEMBER_TOKEN={remember_token}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n")
    logger.info("✅ .env actualizado con nuevo remember token")


def update_render(render_api_key: str, remember_token: str, password: str) -> None:
    """Actualiza TASTYTRADE_REMEMBER_TOKEN en todos los servicios de Render."""
    if not render_api_key:
        logger.info("RENDER_API_KEY no configurada — saltando actualización de Render")
        return

    # Listar servicios
    req = urllib.request.Request(
        "https://api.render.com/v1/services?limit=20",
        headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        services = json.loads(r.read())

    target_names = {"rnd-api", "rnd-streamlit"}
    updated = 0
    for svc in services:
        s = svc.get("service", {})
        if s.get("name") not in target_names:
            continue

        svc_id = s["id"]
        # Actualizar env vars via PATCH
        payload = json.dumps([
            {"key": "TASTYTRADE_REMEMBER_TOKEN", "value": remember_token},
        ]).encode()
        req2 = urllib.request.Request(
            f"https://api.render.com/v1/services/{svc_id}/env-vars",
            data=payload,
            headers={
                "Authorization": f"Bearer {render_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req2, timeout=10) as r:
                r.read()
            logger.info(f"✅ Render {s['name']} actualizado")
            updated += 1
        except Exception as e:
            logger.error(f"❌ Error actualizando Render {s['name']}: {e}")

    if updated == 0:
        logger.warning("No se encontraron servicios rnd-api / rnd-streamlit en Render")


def main():
    parser = argparse.ArgumentParser(description="Renueva token tastytrade y actualiza Render")
    parser.add_argument("--dry-run", action="store_true", help="No guardar cambios")
    args = parser.parse_args()

    env = load_env()

    try:
        session_token, new_remember = renew_token(env)
    except Exception as e:
        logger.error(f"❌ FALLO al renovar token: {e}")
        sys.exit(1)

    if args.dry_run:
        logger.info(f"DRY RUN — session: {session_token[:20]}... remember: {new_remember[:20]}...")
        return

    # Guardar localmente
    TOKEN_FILE.write_text(session_token)
    logger.info(f"✅ /tmp/tt_token.txt actualizado")

    if new_remember:
        update_env_file(new_remember)
        update_render(env.get("RENDER_API_KEY", ""), new_remember, env.get("TASTYTRADE_PASSWORD", ""))
    else:
        logger.warning("No se recibió nuevo remember token — solo session token renovado")

    logger.info("🎉 Renovación completada")


if __name__ == "__main__":
    main()
