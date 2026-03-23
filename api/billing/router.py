from __future__ import annotations

import json
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_user
from api.auth.models import User
from api.core.config import settings
from api.core.database import get_db
from api.credits.service import CreditService
from api.billing.schemas import CheckoutRequest, CheckoutResponse, SubscriptionResponse
from api.billing.service import (
    get_active_subscription_for_user,
    get_subscription_by_stripe_id,
    upsert_subscription,
)
from api.billing.stripe_service import create_checkout_session

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    success = payload.success_url or f"{settings.frontend_base_url}/billing/success"
    cancel = payload.cancel_url or f"{settings.frontend_base_url}/billing/cancel"
    stripe_session, price_id = await create_checkout_session(current_user, payload.plan, success, cancel, session)

    await upsert_subscription(
        session,
        user_id=current_user.id,
        plan=payload.plan,
        status="pending",
        stripe_customer_id=stripe_session.customer,
        stripe_subscription_id=stripe_session.subscription,
        stripe_price_id=price_id,
        period_end=None,
    )
    return CheckoutResponse(url=stripe_session.url)


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    subscription = await get_active_subscription_for_user(session, current_user.id)
    if not subscription:
        return SubscriptionResponse(plan="basic", status="inactive")
    return SubscriptionResponse(
        plan=subscription.plan,
        status=subscription.status,
        current_period_end=subscription.current_period_end,
        stripe_customer_id=subscription.stripe_customer_id,
        stripe_subscription_id=subscription.stripe_subscription_id,
    )


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        if settings.stripe_webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
        else:
            event = stripe.Event.construct_from(json.loads(payload.decode("utf-8")), stripe.api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(data, session)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(data, session)
    elif event_type == "invoice.payment_failed":
        await _handle_payment_failed(data, session)

    return {"received": True}


async def _handle_checkout_completed(data: dict, session: AsyncSession) -> None:
    user_id = data.get("client_reference_id")
    if not user_id:
        return
    plan = data.get("metadata", {}).get("plan", "basic")
    subscription_id = data.get("subscription")
    customer_id = data.get("customer")

    stripe_price_id = None
    period_end = None
    if subscription_id:
        stripe_sub = stripe.Subscription.retrieve(subscription_id)
        if stripe_sub:
            period_end = datetime.fromtimestamp(stripe_sub["current_period_end"], tz=timezone.utc)
            item = stripe_sub["items"]["data"][0]
            price = item.get("price", {})
            stripe_price_id = price.get("id")
            lookup_key = price.get("lookup_key") or "rnd-basic"
            plan = lookup_key.replace("rnd-", "")

    subscription = await upsert_subscription(
        session,
        user_id=int(user_id),
        plan=plan,
        status="active",
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        stripe_price_id=stripe_price_id,
        period_end=period_end,
    )

    credits = CreditService(session)
    await credits.set_plan_allocation(subscription.user_id, plan, f"Asignación plan {plan}")


async def _handle_subscription_deleted(data: dict, session: AsyncSession) -> None:
    subscription_id = data.get("id")
    subscription = await get_subscription_by_stripe_id(session, stripe_subscription_id=subscription_id)
    if not subscription:
        return
    await upsert_subscription(
        session,
        user_id=subscription.user_id,
        plan=subscription.plan,
        status="canceled",
        stripe_customer_id=subscription.stripe_customer_id,
        stripe_subscription_id=subscription_id,
        stripe_price_id=subscription.stripe_price_id,
        period_end=subscription.current_period_end,
    )


async def _handle_payment_failed(data: dict, session: AsyncSession) -> None:
    subscription_id = data.get("subscription")
    if not subscription_id:
        return
    subscription = await get_subscription_by_stripe_id(session, stripe_subscription_id=subscription_id)
    if not subscription:
        return
    await upsert_subscription(
        session,
        user_id=subscription.user_id,
        plan=subscription.plan,
        status="past_due",
        stripe_customer_id=subscription.stripe_customer_id,
        stripe_subscription_id=subscription_id,
        stripe_price_id=subscription.stripe_price_id,
        period_end=subscription.current_period_end,
    )
