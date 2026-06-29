"""Microsoft Entra ID (Azure AD) token validation and identity mapping.

Optional and additive: if ENTRA_TENANT_ID and ENTRA_CLIENT_ID are set, the API
accepts Entra-issued Bearer tokens (used by the Claude connector and, later, the
web-app SSO login) and maps each identity to a CRM user profile, auto-creating
one on first sign-in. If they're NOT set, the API falls back to the existing
X-User-Id header (the current no-auth profile model), so dev and the live app
keep working while this layer is built out.

Token audience: Entra access tokens for a custom API have `aud` = the API's
Application (client) ID or its App ID URI (api://<client-id>). Set
ENTRA_AUDIENCE if yours is the api:// form; it defaults to ENTRA_CLIENT_ID.
"""
import os

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import User

TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")
CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "")
AUDIENCE = os.getenv("ENTRA_AUDIENCE", "") or CLIENT_ID
ENTRA_ENABLED = bool(TENANT_ID and CLIENT_ID)

_ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0" if TENANT_ID else ""
_JWKS_URL = (
    f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
    if TENANT_ID
    else ""
)
# PyJWKClient caches Entra's signing keys between requests.
_jwks_client = PyJWKClient(_JWKS_URL) if ENTRA_ENABLED else None


def validate_entra_token(token: str) -> dict:
    """Verify an Entra-issued JWT (signature, issuer, audience, expiry) and
    return its claims. Raises 401 on any problem, 503 if Entra isn't configured."""
    if not ENTRA_ENABLED:
        raise HTTPException(status_code=503, detail="Entra auth is not configured")
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except HTTPException:
        raise
    except Exception as e:  # signature/issuer/audience/expiry failures
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def user_from_claims(claims: dict, db: Session) -> User:
    """Map a validated token's claims to a CRM profile, creating one on first
    sign-in (keyed by the immutable Entra object id)."""
    oid = claims.get("oid") or claims.get("sub")
    if not oid:
        raise HTTPException(status_code=401, detail="Token has no user identifier")
    email = claims.get("preferred_username") or claims.get("email") or ""
    name = claims.get("name") or email or "M365 user"

    user = db.query(User).filter(User.microsoft_oid == oid).first()
    if user is None:
        user = User(name=name, microsoft_oid=oid, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
