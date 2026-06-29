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
from datetime import date as date_cls
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.orm import Session

import auth
import cards
import models  # noqa: F401 (ensures models are registered on Base)
from database import Base, engine, get_db
from models import Contact, Interaction, User
from schemas import ContactIn, LogIn, UserIn
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
    # Additive, idempotent migration for the card-image columns.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS card_image BYTEA"))
        conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS card_image_type VARCHAR"))
        conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS category_label VARCHAR"))
        # Entra identity columns on users (additive; safe on existing data).
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS microsoft_oid VARCHAR"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_microsoft_oid ON users (microsoft_oid)"))


_ensure_schema()

app = FastAPI(title="Star CRM API", version="0.1.0")

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
        "hasCard": c.card_image is not None,
        "created": c.created_at.date().isoformat() if c.created_at else "",
        "log": [{"date": i.date, "note": i.note} for i in log],
    }


def _store_card(contact: Contact, card_image: str | None) -> None:
    """If a card data URL is provided, downscale and attach it to the contact.
    Absent/blank leaves any existing image untouched."""
    if not card_image:
        return
    raw, _ = cards.parse_data_url(card_image)
    if not raw:
        return
    try:
        contact.card_image = cards.downscale_to_jpeg(raw)
        contact.card_image_type = "image/jpeg"
    except Exception:
        pass  # bad image — keep the contact, just skip the attachment


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


@app.on_event("startup")
def seed_on_first_run() -> None:
    """On an empty database, create the initial 'Bob Bendixen' profile and seed
    his 26 contacts so the app opens populated. New users start blank."""
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
    _store_card(contact, payload.cardImage)
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
    _store_card(contact, payload.cardImage)
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
    plus a compact JPEG data URL to store when the contact is saved. Does not
    persist anything itself — the review-then-save flow handles storage."""
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


@app.get("/api/contacts/{contact_id}/card")
def get_card(
    contact_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    contact = _get_or_404(db, user, contact_id)
    if not contact.card_image:
        raise HTTPException(status_code=404, detail="No card image")
    return Response(
        content=contact.card_image,
        media_type=contact.card_image_type or "image/jpeg",
    )


# --- Claude connector: mount the MCP server at /mcp (when configured) -------
# Registered before the SPA catch-all so its routes aren't intercepted. Guarded
# so a missing MCP SDK or unconfigured Entra never takes down the main API.
try:
    import mcp_server
    app.mount("/mcp", mcp_server.build_mcp_app())
except Exception as _mcp_err:
    import logging
    logging.getLogger("uvicorn.error").info("MCP connector not mounted: %s", _mcp_err)


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

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        """Serve a real static file if it exists; otherwise fall back to
        index.html so client-side routing works."""
        candidate = (_STATIC_DIR / full_path).resolve()
        if candidate.is_file() and _STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
