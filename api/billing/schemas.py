from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

PlanLiteral = Literal["basic", "pro", "enterprise"]


class CheckoutRequest(BaseModel):
    plan: PlanLiteral
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    url: str = Field(..., description="Stripe Checkout URL")


class SubscriptionResponse(BaseModel):
    plan: Optional[PlanLiteral] = None
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
