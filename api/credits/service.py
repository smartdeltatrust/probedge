from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.credits.models import CreditTransaction, CreditWallet

PLAN_CREDIT_ALLOWANCES = {
    "basic": 230,
    "pro": 1000,
    "enterprise": None,
}


class CreditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_wallet(self, user_id: int) -> CreditWallet:
        wallet = await self.session.scalar(select(CreditWallet).where(CreditWallet.user_id == user_id))
        if wallet:
            return wallet
        wallet = CreditWallet(user_id=user_id, balance=0)
        self.session.add(wallet)
        await self.session.commit()
        await self.session.refresh(wallet)
        return wallet

    async def set_plan_allocation(self, user_id: int, plan: str, description: str) -> CreditWallet:
        wallet = await self.get_wallet(user_id)
        allowance = PLAN_CREDIT_ALLOWANCES.get(plan, PLAN_CREDIT_ALLOWANCES["basic"])
        wallet.balance = allowance
        wallet.updated_at = datetime.now(timezone.utc)
        if allowance is not None:
            self.session.add(
                CreditTransaction(
                    user_id=user_id,
                    amount=allowance,
                    type="purchase",
                    description=description,
                )
            )
        await self.session.commit()
        await self.session.refresh(wallet)
        return wallet

    async def add_credits(self, user_id: int, amount: int, description: str, tx_type: str = "purchase") -> CreditWallet:
        wallet = await self.get_wallet(user_id)
        wallet.balance = (wallet.balance or 0) + amount
        wallet.updated_at = datetime.now(timezone.utc)
        self.session.add(
            CreditTransaction(user_id=user_id, amount=amount, type=tx_type, description=description)
        )
        await self.session.commit()
        await self.session.refresh(wallet)
        return wallet

    async def consume(self, user_id: int, amount: int, description: str, plan: str) -> None:
        if plan == "enterprise":
            return

        wallet = await self.get_wallet(user_id)
        if wallet.balance is None:
            return
        if (wallet.balance or 0) < amount:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Créditos insuficientes")
        wallet.balance -= amount
        wallet.updated_at = datetime.now(timezone.utc)
        self.session.add(
            CreditTransaction(user_id=user_id, amount=-amount, type="consume", description=description)
        )
        await self.session.commit()

    async def get_balance(self, user_id: int) -> Optional[int]:
        wallet = await self.get_wallet(user_id)
        return wallet.balance


async def require_credits(
    session: AsyncSession,
    user_id: int,
    amount: int,
    description: str,
    plan: str,
) -> None:
    service = CreditService(session)
    await service.consume(user_id, amount, description, plan)
