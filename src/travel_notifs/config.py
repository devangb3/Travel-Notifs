from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret: str = "dev-only-change-me"
    admin_token: str = "dev-admin"
    base_url: str = "http://localhost:8000"
    database_path: Path = Path("data/travel_notifs.db")
    dry_run: bool = True

    google_maps_api_key: str = ""
    google_routes_api_key: str = ""
    telegram_bot_token: str = ""
    resend_api_key: str = ""
    email_from: str = "Transit Dispatch <alerts@example.com>"

    poll_interval_seconds: int = Field(default=120, ge=30, le=600)
    eta_change_threshold_minutes: int = Field(default=2, ge=1, le=30)
    notification_cooldown_seconds: int = Field(default=120, ge=0, le=3600)

    @property
    def demo_mode(self) -> bool:
        return not self.routes_key

    @property
    def routes_key(self) -> str:
        return self.google_routes_api_key or self.google_maps_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
