from functools import lru_cache
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "RND API"
    app_version: str = "0.2.0"

    # API keys
    massive_api_key: str = ""
    fmp_api_key: str = ""
    finviz_auth_token: str = ""

    # Database & security
    database_url: str = "sqlite+aiosqlite:///./rnd.db"
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    frontend_base_url: str = "http://localhost:3000"

    model_config = {
        "env_file": os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
