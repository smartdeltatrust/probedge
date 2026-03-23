
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_user
from api.auth.models import User
from api.core.database import get_db
from api.core.rate_limit import rate_limit_dependency
from api.credits.models import CreditTransaction
from api.credits.service import CreditService

router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("/wallet", dependencies=[Depends(rate_limit_dependency)])
async def get_wallet(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = CreditService(session)
    wallet = await service.get_wallet(current_user.id)
    return {
        "user_id": current_user.id,
        "plan": getattr(current_user, "current_plan", "basic"),
        "balance": wallet.balance,
        "updated_at": wallet.updated_at,
    }


@router.get("/transactions", dependencies=[Depends(rate_limit_dependency)])
async def get_transactions(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    limit = min(limit, 100)
    result = await session.execute(
        select(CreditTransaction).where(CreditTransaction.user_id == current_user.id).order_by(
            CreditTransaction.created_at.desc()
        ).limit(limit)
    )
    transactions = [tx for tx in result.scalars().all()]
    return {
        "user_id": current_user.id,
        "count": len(transactions),
        "transactions": [
            {
                "id": tx.id,
                "amount": tx.amount,
                "type": tx.type,
                "description": tx.description,
                "created_at": tx.created_at,
            }
            for tx in transactions
        ],
    }
