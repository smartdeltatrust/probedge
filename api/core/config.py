from pydantic_settings import BaseSettings
from functools import lru_cache
import os

class Settings(BaseSettings):
    app_name: str = "RND API"
    app_version: str = "0.1.0"
    massive_api_key: str = ""
    fmp_api_key: str = ""
    finviz_auth_token: str = ""

    model_config = {
        "env_file": os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
        "extra": "ignore",
    }

@lru_cache()
def get_settings() -> Settings:
    return Settings()

# Instancia directa para imports simples
settings = get_settings()
