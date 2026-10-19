from pathlib import Path
from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: PostgresDsn
    TELEGRAM_BOT_TOKEN: SecretStr
    ALLOWED_USER_ID: int
    MAX_CONCURRENT_TASKS: int = 5
    PLAYWRIGHT_HEADLESS: bool = True
    SESSION_STATE_PATH: Path = Path("session.json")
    ESHOP_COUNTRIES: list[str] = ["BR", "US", "AR", "CO", "ZA"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()