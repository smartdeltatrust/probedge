from __future__ import annotations

from typing import Dict, Optional

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User
from api.billing.models import Subscription
from api.core.config import settings

stripe.api_key = settings.stripe_secret_key

PLAN_DETAILS: Dict[str, Dict[str, str]] = {
    "basic": {
        "name": "Basic",
        "amount": 2900,
        "lookup_key": "rnd-basic",
        "description": "Plan basic mensual (230 créditos)",
    },
    "pro": {
        "name": "Pro",
        "amount": 7900,
        "lookup_key": "rnd-pro",
        "description": "Plan pro mensual (1000 créditos)",
    },
    "enterprise": {
        "name": "Enterprise",
        "amount": 17900,
        "lookup_key": "rnd-enterprise",
        "description": "Plan enterprise mensual (créditos ilimitados)",
    },
}


def _require_stripe_key() -> None:
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe no está configurado",
        )


def _ensure_price(plan: str) -> str:
    _require_stripe_key()
    plan_info = PLAN_DETAILS[plan]
    lookup_key = plan_info["lookup_key"]

    prices = stripe.Price.list(active=True, lookup_keys=[lookup_key], limit=1)
    if prices.data:
        return prices.data[0].id

    product = stripe.Product.create(
        name=f"RND {plan_info['name']} Plan",
        description=plan_info["description"],
        metadata={"plan": plan},
    )

    price = stripe.Price.create(
        unit_amount=plan_info["amount"],
        currency="usd",
        recurring={"interval": "month"},
        product=product.id,
        lookup_key=lookup_key,
    )
    return price.id


async def _ensure_customer(user: User, session: AsyncSession) -> str:
    """Busca si el usuario ya tiene un stripe_customer_id guardado."""
    result = await session.scalars(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .where(Subscription.stripe_customer_id.isnot(None))
        .limit(1)
    )
    existing_sub: Optional[Subscription] = result.first()
    if existing_sub and existing_sub.stripe_customer_id:
        return existing_sub.stripe_customer_id

    customer = stripe.Customer.create(email=user.email, metadata={"user_id": user.id})
    return customer.id


async def create_checkout_session(
    user: User,
    plan: str,
    success_url: str,
    cancel_url: str,
    session: AsyncSession,
) -> tuple[stripe.checkout.Session, str]:
    price_id = _ensure_price(plan)
    customer_id = await _ensure_customer(user, session)

    stripe_session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        payment_method_types=["card"],
        client_reference_id=str(user.id),
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"plan": plan},
    )
    return stripe_session, price_id
