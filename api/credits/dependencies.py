from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_user
from api.auth.models import User
from api.core.database import get_db
from api.credits.service import CreditService


def require_credits_dependency(amount: int, description: str):
    async def _dependency(
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> None:
        plan = getattr(current_user, "current_plan", "basic")
        service = CreditService(session)
        await service.consume(current_user.id, amount, description, plan)

    return _dependency
