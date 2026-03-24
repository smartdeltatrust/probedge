# src/adapters/http_client.py
from __future__ import annotations

import json
import ssl
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

import certifi


def get_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Any:
    """
    GET JSON helper (robusto en Windows/Render).
    Evita usar cafile=..., usa SSLContext con certifi.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = Request(url, headers=headers or {})
    with urlopen(req, context=ctx, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)
