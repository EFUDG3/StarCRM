"""Star CRM API — proof of concept backend.

Multi-user with NO authentication. A "user" is just a profile that owns its own
contacts, selected client-side and passed via the X-User-Id header. When SSO
(Entra ID / Microsoft Graph) lands, only how the current user is identified
changes — the per-user data model here stays the same. Do not expose publicly
until that auth layer exists.

Run locally:
    uvicorn main:app --reload
"""
import os
from contextlib import asynccontextmanager
from datetime import date as date_cls
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.orm import Session

import auth
import cards
import chat
import m365
import models  # noqa: F401 (ensures models are registered on Base)
from database import Base, engine, get_db
from models import Contact, Interaction, User
from schemas import ChatIn, ContactIn, LogIn, TodoIn, TodoPatch, UserIn
from seed import SEED_CONTACTS


def _ensure_schema() -> None:
    """Create tables, migrating older contacts tables in place.

    - If the pre-multi-user shape (no user_id) is found, drop and recreate
      (only the demo seed is lost; it's reseeded for 'Bob Bendixen' on startup).
    - Otherwise additively add the card-image columns if they're missing, so
      existing data is preserved when this feature is deployed.
    """
    insp = sa_inspect(engine)
    if insp.has_table("contacts"):
        columns = [c["name"] for c in insp.get_columns("contacts")]
        if "user_id" not in columns:
            Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS category_label VARCHAR"))
        # Card-image storage removed 2026-07 (space + UI cleanup; scans still
        # prefill contacts, the photo just isn't kept). Blobs were exported to
        # ~/Documents/starbot-card-image-backup before this shipped.
        conn.execute(text("ALTER TABLE contacts DROP COLUMN IF EXISTS card_image"))
        conn.execute(text("ALTER TABLE contacts DROP COLUMN IF EXISTS card_image_type"))
        # Entra identity columns on users (additive; safe on existing data).
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS microsoft_oid VARCHAR"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_microsoft_oid ON users (microsoft_oid)"))
        # Starbot chat: per-user MSAL (Graph) token cache. Additive.
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS m365_token_cache TEXT"))
        # Task board: kanban status + priority on todos. Additive; the one-time
        # backfill maps the legacy done flag onto status (done stays synced to
        # status from here on, so this never un-does a reopened task).
        conn.execute(text("ALTER TABLE todos ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'todo'"))
        conn.execute(text("ALTER TABLE todos ADD COLUMN IF NOT EXISTS priority VARCHAR"))
        conn.execute(text("UPDATE todos SET status = 'done' WHERE done = TRUE AND status = 'todo'"))


_ensure_schema()


# Build the Claude connector (MCP) sub-app BEFORE creating the FastAPI app, so
# its StreamableHTTP session-manager lifespan can be propagated into the app's
# own lifespan below (Starlette does NOT run a mounted sub-app's lifespan on its
# own — without this, the first MCP request 500s with "Task group is not
# initialized"). Guarded: a missing MCP SDK or unconfigured Entra simply leaves
# the connector unmounted; the rest of the API is unaffected.
_mcp_app = None
try:
    import mcp_server
    _mcp_app = mcp_server.build_mcp_app()
except Exception as _mcp_err:
    import logging
    logging.getLogger("uvicorn.error").info("MCP connector not mounted: %s", _mcp_err)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown. Seeds the demo profile on an empty DB and, when the
    connector is mounted, runs the MCP session manager's lifespan."""
    _seed_on_first_run()
    if _mcp_app is not None:
        async with _mcp_app.router.lifespan_context(_mcp_app):
            yield
    else:
        yield


app = FastAPI(title="Star CRM API", version="0.1.0", lifespan=lifespan)

# CORS: only the frontend origin(s) may call the API from a browser.
# Override in production via FRONTEND_ORIGINS (comma-separated).
_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def today_iso() -> str:
    return date_cls.today().isoformat()


def serialize(c: Contact) -> dict:
    """Return a contact in the exact shape the React frontend expects.

    Interactions are sorted newest-first so the activity log reads top-down.
    """
    log = sorted(c.interactions, key=lambda i: i.date, reverse=True)
    return {
        "id": c.id,
        "name": c.name,
        "company": c.company or "",
        "role": c.role or "",
        "email": c.email or "",
        "phone": c.phone or "",
        "category": c.category or "bd",
        "categoryLabel": c.category_label or "",
        "nextAction": c.next_action or "",
        "nextDue": c.next_due or "",
        "notes": c.notes or "",
        "created": c.created_at.date().isoformat() if c.created_at else "",
        "log": [{"date": i.date, "note": i.note} for i in log],
    }


def load_seed(db: Session, user: User) -> None:
    """Replace one user's contacts with the original demo set.

    Seed IDs are generated fresh (not taken from the seed data) so multiple
    users can each hold the demo set without primary-key collisions.
    """
    db.query(Contact).filter(Contact.user_id == user.id).delete(
        synchronize_session=False
    )
    for item in SEED_CONTACTS:
        contact = Contact(
            user_id=user.id,
            name=item["name"],
            company=item.get("company", ""),
            role=item.get("role", ""),
            email=item.get("email", ""),
            phone=item.get("phone", ""),
            category=item.get("category", "bd"),
            next_action=item.get("nextAction", ""),
            next_due=item.get("nextDue", ""),
            notes=item.get("notes", ""),
        )
        for entry in item.get("log", []):
            contact.interactions.append(
                Interaction(date=entry["date"], note=entry["note"])
            )
        db.add(contact)
    db.commit()


def get_current_user(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the active user. Prefers an Entra Bearer token (web SSO / the
    Claude connector) when Entra is configured; otherwise falls back to the
    X-User-Id profile header (the current no-auth model), so nothing breaks
    while auth is being rolled out."""
    if auth.ENTRA_ENABLED and authorization and authorization.lower().startswith("bearer "):
        claims = auth.validate_entra_token(authorization.split(" ", 1)[1])
        return auth.user_from_claims(claims, db)
    if x_user_id:
        user = db.get(User, x_user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user
    raise HTTPException(status_code=401, detail="Not authenticated")


def _get_or_404(db: Session, user: User, contact_id: str) -> Contact:
    contact = (
        db.query(Contact)
        .filter(Contact.id == contact_id, Contact.user_id == user.id)
        .first()
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _seed_on_first_run() -> None:
    """On an empty database, create the initial 'Bob Bendixen' profile and seed
    his 26 contacts so the app opens populated. New users start blank. Called
    from the lifespan handler above."""
    db = next(get_db())
    try:
        if db.query(User).count() == 0:
            bob = User(name="Bob Bendixen")
            db.add(bob)
            db.commit()
            db.refresh(bob)
            load_seed(db, bob)
    finally:
        db.close()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# --- Starbot: M365 sign-in, chat, to-dos ------------------------------------
# These routes use the M365 session cookie (m365.get_session_user), NOT the
# X-User-Id header — the chat needs Graph tokens, which hang off the Microsoft
# identity. The CRM board's existing routes are unchanged.
app.include_router(m365.router)


@app.post("/api/chat")
def chat_endpoint(
    payload: ChatIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run one starbot chat turn, streamed as Server-Sent Events. The Graph
    token is minted here (request-scoped session persists any cache refresh);
    the generator then runs on its own DB session while streaming."""
    if not payload.messages:
        raise HTTPException(status_code=400, detail="messages is required")
    graph_token = m365.get_graph_token(user, db)
    return StreamingResponse(
        chat.stream_chat(payload.messages, user.id, graph_token),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/todos")
def list_todos(
    include_done: bool = False,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> list:
    return chat.op_list_todos(db, user, include_done=include_done)


@app.post("/api/todos", status_code=201)
def create_todo(
    payload: TodoIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    created = chat.op_add_todos(db, user, [payload.model_dump()])
    return created[0]


@app.patch("/api/todos/{todo_id}")
def patch_todo(
    todo_id: str,
    payload: TodoPatch,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Partial update: any of text/due/status/priority (the task board), plus
    the legacy `done` boolean (the chat rail's checkbox)."""
    fields = payload.model_dump(exclude_unset=True)
    if "done" in fields:
        fields["status"] = "done" if fields.pop("done") else "todo"
    try:
        return chat.op_update_todo(db, user, todo_id, **fields)
    except ValueError as e:
        code = 404 if "not found" in str(e).lower() else 400
        raise HTTPException(status_code=code, detail=str(e))


@app.delete("/api/todos/{todo_id}", status_code=204)
def remove_todo(
    todo_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    try:
        chat.op_delete_todo(db, user, todo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="To-do not found")


# --- Users (no auth — just selectable profiles) ----------------------------
@app.get("/api/users")
def list_users(db: Session = Depends(get_db)) -> list:
    users = db.query(User).order_by(User.created_at).all()
    return [{"id": u.id, "name": u.name} for u in users]


@app.post("/api/users", status_code=201)
def create_user(payload: UserIn, db: Session = Depends(get_db)) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    user = User(name=name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "name": user.name}


@app.patch("/api/users/{user_id}")
def rename_user(user_id: str, payload: UserIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    user.name = name
    db.commit()
    return {"id": user.id, "name": user.name}


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: str, db: Session = Depends(get_db)) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)  # contacts + interactions cascade
    db.commit()


# --- Contacts (scoped to the active user via X-User-Id) --------------------
@app.get("/api/contacts")
def list_contacts(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list:
    rows = db.query(Contact).filter(Contact.user_id == user.id).all()
    return [serialize(c) for c in rows]


@app.post("/api/contacts", status_code=201)
def create_contact(
    payload: ContactIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    contact = Contact(
        user_id=user.id,
        name=payload.name,
        company=payload.company,
        role=payload.role,
        email=payload.email,
        phone=payload.phone,
        category=payload.category,
        category_label=payload.categoryLabel,
        next_action=payload.nextAction,
        next_due=payload.nextDue,
        notes=payload.notes,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return serialize(contact)


@app.get("/api/contacts/{contact_id}")
def get_contact(
    contact_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return serialize(_get_or_404(db, user, contact_id))


@app.put("/api/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    payload: ContactIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    contact = _get_or_404(db, user, contact_id)
    contact.name = payload.name
    contact.company = payload.company
    contact.role = payload.role
    contact.email = payload.email
    contact.phone = payload.phone
    contact.category = payload.category
    contact.category_label = payload.categoryLabel
    contact.next_action = payload.nextAction
    contact.next_due = payload.nextDue
    contact.notes = payload.notes
    db.commit()
    db.refresh(contact)
    return serialize(contact)


@app.delete("/api/contacts/{contact_id}", status_code=204)
def delete_contact(
    contact_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    contact = _get_or_404(db, user, contact_id)
    db.delete(contact)
    db.commit()


@app.post("/api/contacts/{contact_id}/log")
def log_touch(
    contact_id: str,
    payload: LogIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    contact = _get_or_404(db, user, contact_id)
    contact.interactions.append(
        Interaction(date=payload.date or today_iso(), note=payload.note)
    )
    db.commit()
    db.refresh(contact)
    return serialize(contact)


@app.post("/api/contacts/{contact_id}/complete")
def complete_action(
    contact_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Log the current next action as done and clear it."""
    contact = _get_or_404(db, user, contact_id)
    done_note = "Done: " + (contact.next_action or "follow-up")
    contact.interactions.append(Interaction(date=today_iso(), note=done_note))
    contact.next_action = ""
    contact.next_due = ""
    db.commit()
    db.refresh(contact)
    return serialize(contact)


@app.post("/api/reset")
def reset(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Restore the demo contact set for the active user only."""
    load_seed(db, user)
    count = db.query(Contact).filter(Contact.user_id == user.id).count()
    return {"status": "reset", "count": count}


@app.post("/api/contacts/scan-card")
async def scan_card(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    """Read a business-card photo with Claude vision and return prefill fields
    plus a JPEG data URL the review form shows for verification. The image is
    NEVER persisted (card-image storage was removed to save DB space)."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        jpeg = cards.downscale_to_jpeg(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read that image")
    try:
        fields = cards.extract_fields(jpeg)
    except RuntimeError as e:  # API key missing
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Card extraction failed: {e}")
    fields["cardImage"] = cards.to_data_url(jpeg)
    return fields


# --- Claude connector: mount the MCP server at /mcp (when configured) -------
# Built above (so its lifespan is wired into the FastAPI app). Mounted here,
# before the SPA catch-all, so its routes aren't intercepted. The sub-app serves
# its endpoint at "/", so mounting at "/mcp" exposes the connector at "/mcp".
if _mcp_app is not None:
    from fastapi.responses import JSONResponse, RedirectResponse

    # A bare "/mcp" (no trailing slash) would otherwise fall through to the SPA
    # catch-all and return 405/404, because the MCP app is mounted at "/mcp/".
    # Redirect it (307 preserves method + body) so a connector URL without the
    # trailing slash still reaches the endpoint. Registered BEFORE the mount so it
    # wins the exact "/mcp" match; "/mcp/..." still routes into the mount.
    @app.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
    def _mcp_trailing_slash() -> RedirectResponse:
        return RedirectResponse("/mcp/", status_code=307)

    app.mount("/mcp", _mcp_app)

    # OAuth Authorization Server metadata shim. Microsoft Entra publishes no RFC
    # 8414 document and omits `code_challenge_methods_supported`, so Claude's
    # connector can't discover it as the auth server and falls back to guessing
    # /authorize on our own origin. We publish a compliant metadata doc at our
    # origin that points Claude straight at Microsoft's real authorize/token
    # endpoints and explicitly advertises PKCE S256. Served under both the OAuth
    # and OIDC well-known names because connectors probe either. The protected-
    # resource metadata (from mcp_server.py) advertises this origin as the
    # authorization server, so Claude lands here.

    _AS_ORIGIN = os.getenv("MCP_RESOURCE_URL", "http://localhost:8000/mcp").rsplit("/mcp", 1)[0]
    # Match the trailing slash that AnyHttpUrl puts on authorization_servers in the
    # protected-resource metadata; RFC 8414 requires issuer to match byte-for-byte.
    _AS_ISSUER = _AS_ORIGIN.rstrip("/") + "/"
    _ENTRA_OAUTH = f"https://login.microsoftonline.com/{auth.TENANT_ID}/oauth2/v2.0"
    _AS_METADATA = {
        "issuer": _AS_ISSUER,
        "authorization_endpoint": f"{_ENTRA_OAUTH}/authorize",
        "token_endpoint": f"{_ENTRA_OAUTH}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post", "client_secret_basic",
        ],
        "scopes_supported": [
            f"api://{auth.CLIENT_ID}/access_as_user",
            "openid", "profile", "offline_access",
        ],
    }

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    @app.get("/.well-known/openid-configuration", include_in_schema=False)
    def oauth_authorization_server_metadata() -> JSONResponse:
        return JSONResponse(_AS_METADATA)


# --- Serve the built frontend (single-service deploy) -----------------------
# The Docker build copies Vite's output to ./static, so one Cloud Run service
# serves both the API (above) and the site. When ./static is absent (local
# API-only dev), this block is skipped and only the API runs.
#
# These routes are registered last so the /api/* and /docs routes always match
# first; the catch-all only handles everything else.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    # Path heads that must NEVER fall back to the SPA. OAuth/MCP discovery probes
    # (well-known docs, /authorize, /token, /register, …) have to return a real 404
    # so Claude's connector follows the protected-resource metadata to Microsoft
    # Entra instead of mistaking the React app (served as HTTP 200 HTML) for an
    # authorization server. /api and /mcp are handled by real routes above; listed
    # here defensively so they can never resolve to index.html either.
    _NON_SPA_HEADS = {
        "authorize", "token", "register", "revoke", "introspect", "mcp", "api",
    }

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        """Serve a real static file if it exists; otherwise fall back to
        index.html so the state-driven UI loads on a deep link. OAuth/MCP/API
        paths get a real 404 instead of the SPA (see _NON_SPA_HEADS)."""
        head = full_path.split("/", 1)[0]
        if full_path.startswith(".well-known") or head in _NON_SPA_HEADS:
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_STATIC_DIR / full_path).resolve()
        if candidate.is_file() and _STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
