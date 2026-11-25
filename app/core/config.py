from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
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

    # Job backend: "arq" (Redis worker; local dev / any always-on host) or "cloudtasks"
    # (Cloud Tasks + Cloud Scheduler call /internal/* on this service; scales to zero).
    queue_backend: Literal["arq", "cloudtasks"] = "arq"
    redis_url: str = "redis://localhost:6379/0"
    gcp_project: str = ""
    gcp_location: str = ""  # region of the Cloud Tasks queue (same as Cloud Run)
    cloud_tasks_queue: str = "pulse-jobs"
    # Public https URL of this service; used as the task target and as the OIDC audience.
    service_url: str = ""
    # Service account that Cloud Tasks / Scheduler sign their OIDC tokens as.
    tasks_invoker_sa: str = ""

    # Optional override (base64 Ed25519 seed) for /v1/config; default derives from FERNET_KEYS[0].
    config_signing_key: str = ""

    supabase_jwt_secret: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str = ""

    # Dev-only: exposes POST /v1/dev/token, which mints a JWT for a fixed local user so the app
    # can talk to a local backend without a real Supabase login. Refused outside local dev.
    allow_dev_auth: bool = False
    dev_auth_user_id: str = "00000000-0000-4000-8000-00000000d3e1"
    dev_auth_email: str = "dev@pulse.local"

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

    @model_validator(mode="after")
    def _dev_auth_only_locally(self) -> "Settings":
        if self.allow_dev_auth:
            if self.app_env not in ("development", "test"):
                raise ValueError(
                    "ALLOW_DEV_AUTH is only permitted with APP_ENV=development; "
                    f"refusing to start with APP_ENV={self.app_env}"
                )
            if not self.supabase_jwt_secret:
                raise ValueError("ALLOW_DEV_AUTH needs SUPABASE_JWT_SECRET to sign dev tokens")
        return self

    @property
    def state_secret(self) -> str:
        return self.oauth_state_secret or (self.fernet_keys[0] if self.fernet_keys else "")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
