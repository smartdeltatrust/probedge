from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_user
from api.auth.models import User
from api.auth.schemas import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserProfile,
)
from api.auth.service import authenticate_user, create_user
from api.billing.service import get_active_subscription_for_user
from api.core.config import settings
from api.core.database import get_db
from api.core.security import create_access_token, create_refresh_token, decode_token
from api.credits.service import CreditService

router = APIRouter(prefix="/auth", tags=["auth"])


async def _build_user_profile(user: User, session: AsyncSession) -> UserProfile:
    subscription = await get_active_subscription_for_user(session, user.id)
    plan = subscription.plan if subscription else "basic"
    credits = CreditService(session)
    wallet = await credits.get_wallet(user.id)
    return UserProfile(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        plan=plan,
        credit_balance=wallet.balance,
        created_at=user.created_at,
    )


def _issue_tokens(user: User) -> TokenPair:
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    expires_in = settings.access_token_expire_minutes * 60
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db)):
    user = await create_user(session, payload.email, payload.password)
    profile = await _build_user_profile(user, session)
    tokens = _issue_tokens(user)
    return AuthResponse(user=profile, tokens=tokens)


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db)):
    user = await authenticate_user(session, payload.email, payload.password)
    profile = await _build_user_profile(user, session)
    tokens = _issue_tokens(user)
    return AuthResponse(user=profile, tokens=tokens)


@router.post("/refresh", response_model=TokenPair)
async def refresh_token(payload: RefreshRequest, session: AsyncSession = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if data.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token inválido")
    user = await session.get(User, int(data["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    return _issue_tokens(user)


@router.get("/me", response_model=UserProfile)
async def me(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    return await _build_user_profile(current_user, session)
