"""Runtime configuration, loaded from environment variables / .env (see .env.example).

Only the Phase 1 real-time core's settings are load-bearing here; the rest default to
empty/None so this module — and everything that only needs Solana connectivity —
works standalone before the database, Discord bot, or market-data providers exist.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: str = ""
    discord_alert_channel_id: int | None = None

    solana_rpc_http: str = "https://api.mainnet-beta.solana.com"
    solana_rpc_ws: str = "wss://api.mainnet-beta.solana.com"

    helius_api_key: str = ""

    database_url: str = ""
    redis_url: str = ""

    pump_api_key: str = ""
    dex_api_key: str = ""

    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
