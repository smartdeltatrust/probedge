from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, status
from limits import parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import MovingWindowRateLimiter

storage = MemoryStorage()
strategy = MovingWindowRateLimiter(storage)

PLAN_LIMITS = {
    "anonymous": "20/hour",
    "basic": "50/hour",
    "pro": "200/hour",
    "enterprise": None,
}


async def enforce_rate_limit(request: Request, plan: Optional[str], identifier: str) -> None:
    limit_value = PLAN_LIMITS.get(plan or "basic", PLAN_LIMITS["basic"])
    if not limit_value:
        return
    limit = parse(limit_value)
    # `hit` returns True if the request is allowed (not over limit)
    allowed = await strategy.hit(limit, identifier)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit excedido")


async def rate_limit_dependency(request: Request) -> None:
    plan = getattr(request.state, "rate_plan", "basic")
    identifier = getattr(request.state, "rate_limit_id", None)
    if not identifier:
        client_host = request.client.host if request.client else "anonymous"
        identifier = f"ip:{client_host}"
    await enforce_rate_limit(request, plan or "basic", identifier)
