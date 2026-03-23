from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.billing.models import Subscription


async def get_active_subscription_for_user(session: AsyncSession, user_id: int) -> Optional[Subscription]:
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
    )
    subscriptions = result.scalars().all()
    for sub in subscriptions:
        if sub.is_active:
            return sub
    return None


async def upsert_subscription(
    session: AsyncSession,
    *,
    user_id: int,
    plan: str,
    status: str,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
    stripe_price_id: Optional[str] = None,
    period_end: Optional[datetime] = None,
) -> Subscription:
    subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
    )
    if not subscription:
        subscription = Subscription(user_id=user_id, plan=plan)
        session.add(subscription)

    subscription.plan = plan
    subscription.status = status
    subscription.stripe_customer_id = stripe_customer_id
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.stripe_price_id = stripe_price_id
    subscription.current_period_end = period_end
    subscription.updated_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(subscription)
    return subscription


async def get_subscription_by_stripe_id(
    session: AsyncSession,
    *,
    stripe_subscription_id: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
) -> Optional[Subscription]:
    stmt = select(Subscription)
    if stripe_subscription_id:
        stmt = stmt.where(Subscription.stripe_subscription_id == stripe_subscription_id)
    if stripe_customer_id and not stripe_subscription_id:
        stmt = stmt.where(Subscription.stripe_customer_id == stripe_customer_id)
    result = await session.execute(stmt)
    return result.scalars().first()
