"""Microsoft 365 sign-in + delegated Graph tokens for the starbot chat.

Server-side OAuth (authorization-code flow) with MSAL as a confidential
client, reusing the existing Entra app registration. The browser never sees a
token: /api/auth/login redirects to Microsoft, /api/auth/callback exchanges
the code, and the session is a signed-JWT cookie holding only the user id.
Each user's MSAL token cache (refresh + access tokens for Graph) is stored on
their row (users.m365_token_cache), so tool calls can silently mint fresh
Graph tokens on demand — no background jobs, tokens are used only while the
user is chatting.

Not blocked by DNS: Entra redirect URIs may be any https URL (or http for
localhost), unlike App ID URIs. Set PUBLIC_BASE_URL to the deployed origin.

Requires (on top of auth.py's ENTRA_TENANT_ID / ENTRA_CLIENT_ID):
  ENTRA_CLIENT_SECRET  confidential-client secret from the app registration
  SESSION_SECRET       signs the session cookie; ephemeral if unset (dev)
  PUBLIC_BASE_URL      e.g. https://starbot.<env>.azurecontainerapps.io
"""
import logging
import os
import secrets
import time

import jwt
import msal
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

import auth
from database import get_db
from models import User

log = logging.getLogger("uvicorn.error")

CLIENT_SECRET = os.getenv("ENTRA_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
REDIRECT_URI = f"{PUBLIC_BASE_URL}/api/auth/callback"
# Where to land after sign-in. "/#starbot" opens the chat tab; local dev needs
# the Vite origin because the callback runs on :8000 but the site is on :5173.
POST_LOGIN_REDIRECT = os.getenv("POST_LOGIN_REDIRECT", "/#starbot")

SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    log.info("SESSION_SECRET not set — using an ephemeral secret (sessions reset on restart)")

SESSION_COOKIE = "starbot_session"
SESSION_TTL_S = 14 * 24 * 3600
_FLOW_COOKIE = "starbot_msal_flow"
_SECURE_COOKIES = PUBLIC_BASE_URL.startswith("https://")

# Delegated Graph scopes. MSAL adds openid/profile/offline_access itself.
# Sites.Read.All (SharePoint search) needs one-time admin consent in Entra.
GRAPH_SCOPES = [
    "User.Read",
    "Mail.Read",
    "Calendars.Read",
    "Sites.Read.All",
    "Files.Read.All",
]

M365_LOGIN_ENABLED = bool(auth.ENTRA_ENABLED and CLIENT_SECRET)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _build_msal_app(cache: msal.SerializableTokenCache | None = None) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        auth.CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{auth.TENANT_ID}",
        client_credential=CLIENT_SECRET,
        token_cache=cache,
    )


def _sign(payload: dict, ttl_s: int) -> str:
    return jwt.encode({**payload, "exp": int(time.time()) + ttl_s}, SESSION_SECRET, algorithm="HS256")


def _verify(token: str) -> dict | None:
    try:
        return jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def get_session_user(
    starbot_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the M365-signed-in user from the session cookie. Starbot chat and
    to-do routes depend on this (they need Graph tokens, which hang off the
    M365 identity); the CRM board keeps its existing X-User-Id/Bearer model."""
    if not starbot_session:
        raise HTTPException(status_code=401, detail="Not signed in")
    claims = _verify(starbot_session)
    if not claims or not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Session expired — sign in again")
    user = db.get(User, claims["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Session user no longer exists")
    return user


def get_graph_token(user: User, db: Session) -> str:
    """Silently acquire a delegated Graph access token from the user's stored
    MSAL cache (refreshing if needed). Raises 401 when re-sign-in is required."""
    if not M365_LOGIN_ENABLED:
        raise HTTPException(status_code=503, detail="Microsoft 365 sign-in is not configured")
    if not user.m365_token_cache:
        raise HTTPException(status_code=401, detail="No Microsoft 365 link — sign in again")
    cache = msal.SerializableTokenCache()
    cache.deserialize(user.m365_token_cache)
    app = _build_msal_app(cache)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0]) if accounts else None
    if cache.has_state_changed:
        user.m365_token_cache = cache.serialize()
        db.commit()
    if not result or "access_token" not in result:
        raise HTTPException(status_code=401, detail="Microsoft 365 session expired — sign in again")
    return result["access_token"]


@router.get("/login")
def login() -> RedirectResponse:
    """Kick off the Microsoft sign-in redirect. The MSAL 'flow' (state + PKCE
    verifier) rides in a short-lived signed cookie back to the callback."""
    if not M365_LOGIN_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Microsoft 365 sign-in is not configured (ENTRA_TENANT_ID / "
                   "ENTRA_CLIENT_ID / ENTRA_CLIENT_SECRET)",
        )
    flow = _build_msal_app().initiate_auth_code_flow(GRAPH_SCOPES, redirect_uri=REDIRECT_URI)
    resp = RedirectResponse(flow["auth_uri"], status_code=302)
    resp.set_cookie(
        _FLOW_COOKIE, _sign({"flow": flow}, ttl_s=600),
        max_age=600, httponly=True, samesite="lax", secure=_SECURE_COOKIES,
    )
    return resp


@router.get("/callback")
def callback(
    request: Request,
    starbot_msal_flow: str | None = Cookie(default=None, alias=_FLOW_COOKIE),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Exchange the auth code, link/provision the profile, store the Graph
    token cache, set the session cookie, and land on the chat tab."""
    wrapped = _verify(starbot_msal_flow) if starbot_msal_flow else None
    if not wrapped or "flow" not in wrapped:
        raise HTTPException(status_code=400, detail="Sign-in flow expired — try again")
    cache = msal.SerializableTokenCache()
    app = _build_msal_app(cache)
    try:
        result = app.acquire_token_by_auth_code_flow(wrapped["flow"], dict(request.query_params))
    except ValueError as e:  # state mismatch etc.
        raise HTTPException(status_code=400, detail=f"Sign-in failed: {e}")
    if "error" in result:
        raise HTTPException(
            status_code=401,
            detail=f"Sign-in failed: {result.get('error_description') or result['error']}",
        )

    # id_token_claims carries oid / name / preferred_username — the same shape
    # auth.user_from_claims already maps to a profile (creating one on first use).
    user = auth.user_from_claims(result.get("id_token_claims") or {}, db)
    user.m365_token_cache = cache.serialize()
    db.commit()

    resp = RedirectResponse(POST_LOGIN_REDIRECT, status_code=302)
    resp.delete_cookie(_FLOW_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE, _sign({"sub": user.id}, ttl_s=SESSION_TTL_S),
        max_age=SESSION_TTL_S, httponly=True, samesite="lax", secure=_SECURE_COOKIES,
    )
    return resp


@router.get("/me")
def me(
    starbot_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> dict:
    """Session probe for the frontend. Never 401s — the chat tab uses this to
    decide between the sign-in card and the chat window."""
    if not M365_LOGIN_ENABLED:
        return {"signedIn": False, "configured": False}
    if starbot_session:
        claims = _verify(starbot_session)
        user = db.get(User, claims["sub"]) if claims and claims.get("sub") else None
        if user is not None:
            return {
                "signedIn": True, "configured": True,
                "userId": user.id, "name": user.name, "email": user.email or "",
            }
    return {"signedIn": False, "configured": True}


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"signedIn": False})
    resp.delete_cookie(SESSION_COOKIE)
    return resp
