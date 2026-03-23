from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User
from api.core.database import get_db
from api.core.security import decode_token
from api.billing.service import get_active_subscription_for_user

bearer_scheme = HTTPBearer(auto_error=False)


async def _extract_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Optional[str]:
    if not credentials:
        return None
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Esquema inválido")
    return credentials.credentials


async def _resolve_user(
    request: Request,
    session: AsyncSession,
    token: Optional[str],
    *,
    require_active: bool,
) -> Optional[User]:
    if not token:
        request.state.rate_plan = getattr(request.state, "rate_plan", "basic")
        if not getattr(request.state, "rate_limit_id", None):
            client_host = request.client.host if request.client else "anonymous"
            request.state.rate_limit_id = f"ip:{client_host}"
        return None

    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    user = await session.get(User, int(user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    if require_active and not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cuenta desactivada")

    subscription = await get_active_subscription_for_user(session, user.id)
    plan = subscription.plan if subscription else "basic"

    request.state.rate_plan = plan
    request.state.rate_limit_id = f"user:{user.id}"
    request.state.current_user = user
    request.state.subscription = subscription
    setattr(user, "current_plan", plan)
    setattr(user, "current_subscription", subscription)

    return user


async def get_current_user(
    request: Request,
    token: str = Depends(_extract_token),
    session: AsyncSession = Depends(get_db),
) -> User:
    user = await _resolve_user(request, session, token, require_active=True)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    return user


async def get_optional_current_user(
    request: Request,
    token: Optional[str] = Depends(_extract_token),
    session: AsyncSession = Depends(get_db),
) -> Optional[User]:
    return await _resolve_user(request, session, token, require_active=True)
