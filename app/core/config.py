from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "pulse-api"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    default_currency: str = "AED"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pulse"
    db_pgbouncer: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"

    supabase_jwt_secret: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str = ""

    fernet_keys: Annotated[list[str], NoDecode] = []

    # --- file storage (statement PDFs) ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "./var/storage"
    # Supabase Storage exposes an S3-compatible endpoint:
    #   https://<ref>.supabase.co/storage/v1/s3  (region e.g. eu-central-1)
    s3_endpoint_url: str = ""
    s3_bucket: str = "statements"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    max_upload_bytes: int = 15 * 1024 * 1024

    # --- Gmail OAuth (statement ingestion) ---
    google_client_id: str = ""
    google_client_secret: str = ""
    # Must be registered in the Google Cloud console, e.g. https://api.example.com/v1/email-connections/gmail/callback
    google_redirect_uri: str = ""
    # Where to send the browser after the callback (app deep link / web page). Empty => JSON response.
    oauth_success_redirect: str = ""
    # HMAC key for the OAuth `state` parameter; falls back to the first Fernet key.
    oauth_state_secret: str = ""
    gmail_initial_lookback_days: int = 180
    gmail_sync_overlap_days: int = 3
    gmail_max_messages_per_sync: int = 100

    # --- live valuations (gold, FX, property index); failures always fall back to the manual value ---
    gold_provider: Literal["goldapi", "none"] = "goldapi"
    fx_provider: Literal["open_er_api", "peg", "none"] = "open_er_api"
    # JSON: {"dubai_apartments": {"2022-01-01": 100, "2026-01-01": 148.5}}. No free property API is wired in.
    property_index_json: str = ""
    valuation_timeout_seconds: float = 10.0
    crypto_provider: Literal["coingecko", "none"] = "coingecko"
    stock_provider: Literal["stooq", "none"] = "stooq"
    fund_provider: Literal["mfapi", "none"] = "mfapi"
    price_ttl_seconds: int = (
        900  # snapshot data older than this is refreshed on request (stale kept on error)
    )
    prices_refresh_on_request: bool = True

    @field_validator("cors_origins", "fernet_keys", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        # Supabase hands out postgresql:// / postgres:// URLs; we need the asyncpg driver.
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix) :]
        return v

    @property
    def state_secret(self) -> str:
        return self.oauth_state_secret or (self.fernet_keys[0] if self.fernet_keys else "")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
