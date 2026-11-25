"""Dev-only sign-in bypass. Only mounted when ALLOW_DEV_AUTH=true (Settings refuses that outside
APP_ENV=development), so production never exposes this route.

POST /v1/dev/token returns a short-lived HS256 JWT for a fixed local user, signed with
SUPABASE_JWT_SECRET, which the normal auth path then accepts like any Supabase token.
"""

import time

import jwt
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import SettingsDep

router = APIRouter(prefix="/dev", tags=["dev"])

TOKEN_TTL_SECONDS = 24 * 3600


class DevToken(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = TOKEN_TTL_SECONDS
    user_id: str


@router.post("/token", response_model=DevToken)
async def dev_token(settings: SettingsDep) -> DevToken:
    now = int(time.time())
    payload = {
        "sub": settings.dev_auth_user_id,
        "email": settings.dev_auth_email,
        "role": "authenticated",
        "aud": settings.supabase_jwt_audience,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    if settings.supabase_jwt_issuer:
        payload["iss"] = settings.supabase_jwt_issuer
    token = jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")
    return DevToken(access_token=token, user_id=settings.dev_auth_user_id)
