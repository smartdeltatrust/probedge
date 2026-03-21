from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "RND Probabilities API"
    app_version: str = "0.1.0"
    debug: bool = False

    # API Keys (loaded from .env)
    massive_api_key: str = ""
    fmp_api_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
