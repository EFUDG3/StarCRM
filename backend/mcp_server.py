"""Claude custom connector — remote MCP server for Star CRM.

Exposes CRUD tools over the SAME Postgres database the web app uses, scoped to
the authenticated Microsoft Entra (M365) user. Mounted at /mcp on the FastAPI
app (see main.py) and reached by Claude over HTTPS from Anthropic's cloud.

Design:
- The op_* functions are transport-agnostic and unit-testable on their own —
  they take a DB session + the resolved user and do the work.
- The @mcp.tool wrappers resolve the user from the Entra Bearer token Claude
  sends, then call the matching op_*.
- Tool docstrings are written so Claude asks for missing notes/follow-up dates
  and searches for duplicates before creating, per the desired conversational UX.

NOTE: the OAuth/resource-metadata wiring (AuthSettings) needs the real Entra
values and the deployed MCP URL to finalize — see build_mcp_app(). The CRM
logic below does not depend on that and is fully exercised by the tests.
"""
# NOTE: do NOT add `from __future__ import annotations` here. FastMCP's
# Tool.from_function inspects raw parameter annotations (issubclass(..., Context)).
# Stringized annotations (PEP 563) turn `str` into "str", so get_origin() is None
# and issubclass() throws 'issubclass() arg 1 must be a class' — the connector then
# fails to mount. Python 3.13 evaluates the `X | None` syntax below natively anyway.
from datetime import date as date_cls
from typing import Optional

from sqlalchemy.orm import Session

import auth
from database import SessionLocal
from models import Contact, Interaction, User

VALID_CATEGORIES = {
    "bd", "gc", "vendor", "property", "client", "sub", "designer", "insurance", "other",
}


def _today() -> str:
    return date_cls.today().isoformat()


def _serialize(c: Contact) -> dict:
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


def _scoped(db: Session, user: User, contact_id: str) -> Contact | None:
    return (
        db.query(Contact)
        .filter(Contact.id == contact_id, Contact.user_id == user.id)
        .first()
    )


# --- Transport-agnostic operations (unit-testable) -------------------------
def op_search(db: Session, user: User, query: str = "") -> list:
    """Return the user's contacts, optionally filtered by a case-insensitive
    substring over name/company/role/email/notes."""
    rows = db.query(Contact).filter(Contact.user_id == user.id).all()
    if query:
        ql = query.lower()
        rows = [
            c for c in rows
            if ql in " ".join(
                [c.name or "", c.company or "", c.role or "", c.email or "", c.notes or ""]
            ).lower()
        ]
    return [_serialize(c) for c in rows]


def op_get(db: Session, user: User, contact_id: str) -> dict:
    c = _scoped(db, user, contact_id)
    if c is None:
        raise ValueError("Contact not found")
    return _serialize(c)


def op_create(
    db: Session,
    user: User,
    name: str,
    company: str = "",
    role: str = "",
    email: str = "",
    phone: str = "",
    category: str = "bd",
    category_label: str = "",
    next_action: str = "",
    next_due: str = "",
    notes: str = "",
) -> dict:
    if not name or not name.strip():
        raise ValueError("name is required")
    cat = category if category in VALID_CATEGORIES else "other"
    c = Contact(
        user_id=user.id,
        name=name.strip(),
        company=company,
        role=role,
        email=email,
        phone=phone,
        category=cat,
        category_label=category_label,
        next_action=next_action,
        next_due=next_due,
        notes=notes,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize(c)


def op_update(db: Session, user: User, contact_id: str, **fields) -> dict:
    c = _scoped(db, user, contact_id)
    if c is None:
        raise ValueError("Contact not found")
    mapping = {
        "name": "name", "company": "company", "role": "role", "email": "email",
        "phone": "phone", "category": "category", "category_label": "category_label",
        "next_action": "next_action", "next_due": "next_due", "notes": "notes",
    }
    for key, attr in mapping.items():
        if key in fields and fields[key] is not None:
            value = fields[key]
            if attr == "category" and value not in VALID_CATEGORIES:
                value = "other"
            setattr(c, attr, value)
    db.commit()
    db.refresh(c)
    return _serialize(c)


def op_delete(db: Session, user: User, contact_id: str) -> dict:
    c = _scoped(db, user, contact_id)
    if c is None:
        raise ValueError("Contact not found")
    name = c.name
    db.delete(c)
    db.commit()
    return {"deleted": True, "id": contact_id, "name": name}


def op_log_touch(db: Session, user: User, contact_id: str, note: str, date: str = "") -> dict:
    c = _scoped(db, user, contact_id)
    if c is None:
        raise ValueError("Contact not found")
    c.interactions.append(Interaction(date=date or _today(), note=note))
    db.commit()
    db.refresh(c)
    return _serialize(c)


# --- MCP server (transport + auth) -----------------------------------------
def _resolve_user(db: Session) -> User:
    """Resolve the authenticated Entra user for the current MCP request.

    Reads the Bearer token from the active request context, validates it via
    auth.py, and maps it to a CRM profile (auto-provisioning on first use)."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    access = get_access_token()
    if access is None or not getattr(access, "token", None):
        raise ValueError("Not authenticated")
    claims = auth.validate_entra_token(access.token)
    return auth.user_from_claims(claims, db)


def build_mcp_app():
    """Construct the FastMCP server and return its Streamable-HTTP ASGI app for
    mounting at /mcp. Raises if the MCP SDK or Entra config isn't ready, so
    main.py can mount it conditionally."""
    import os

    from mcp.server.fastmcp import FastMCP
    from mcp.server.auth.provider import TokenVerifier, AccessToken
    from mcp.server.auth.settings import AuthSettings

    if not auth.ENTRA_ENABLED:
        raise RuntimeError("Entra not configured; MCP connector disabled")

    # Public URL of this MCP resource (the mounted /mcp). Set MCP_RESOURCE_URL
    # to the deployed value, e.g. https://star-crm-xxxxx.us-west1.run.app/mcp.
    resource_url = os.getenv("MCP_RESOURCE_URL", "http://localhost:8000/mcp")

    class EntraTokenVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> AccessToken | None:
            try:
                claims = auth.validate_entra_token(token)
            except Exception:
                return None
            oid = claims.get("oid") or claims.get("sub") or "unknown"
            # Entra stamps only the SHORT scope name (e.g. "access_as_user") into the
            # token's `scp`. RequireAuthMiddleware checks required_scopes (the full
            # api://<client-id>/access_as_user URI, which Claude must REQUEST) against
            # this list, so expose both the short names and their fully-qualified forms.
            short = (claims.get("scp") or "").split()
            scopes = short + [f"api://{auth.CLIENT_ID}/{s}" for s in short]
            return AccessToken(
                token=token, client_id=oid, scopes=scopes, expires_at=claims.get("exp")
            )

    # Our own origin (…/mcp -> …). We advertise THIS as the authorization server,
    # not Entra directly: main.py serves an RFC 8414 metadata doc here that points
    # Claude at Microsoft's real authorize/token endpoints and advertises PKCE
    # S256. Entra's own metadata omits S256 and has no RFC 8414 doc, so Claude
    # can't discover it and otherwise falls back to guessing /authorize on us.
    origin = resource_url.rsplit("/mcp", 1)[0] or resource_url

    mcp = FastMCP(
        "Star CRM",
        token_verifier=EntraTokenVerifier(),
        auth=AuthSettings(
            issuer_url=origin,
            resource_server_url=resource_url,
            # Advertise the FULL scope URI so it lands in the protected-resource
            # metadata's scopes_supported and Claude requests it. Microsoft rejects a
            # bare "access_as_user" and rejects an offline_access-only request with no
            # resource scope, so this value is what makes the token request valid.
            # EntraTokenVerifier reports this same full URI as a held scope (Entra only
            # puts the short name in `scp`), so the middleware check passes.
            required_scopes=[f"api://{auth.CLIENT_ID}/access_as_user"],
        ),
        # Serve the MCP endpoint at the sub-app root so that mounting this app at
        # "/mcp" in main.py yields the endpoint at "/mcp" (not "/mcp/mcp"). The
        # default "/mcp" path would double under the mount. MCP_RESOURCE_URL must
        # therefore end in "/mcp" so the advertised resource-metadata URL lines up.
        streamable_http_path="/",
    )

    @mcp.tool()
    def search_contacts(query: str = "") -> list:
        """Search the current user's contacts by name, company, role, email, or
        notes. Call this BEFORE create_contact to avoid adding a duplicate."""
        db = SessionLocal()
        try:
            return op_search(db, _resolve_user(db), query)
        finally:
            db.close()

    @mcp.tool()
    def get_contact(contact_id: str) -> dict:
        """Get one contact by id, including its activity log."""
        db = SessionLocal()
        try:
            return op_get(db, _resolve_user(db), contact_id)
        finally:
            db.close()

    @mcp.tool()
    def create_contact(
        name: str,
        company: str = "",
        role: str = "",
        email: str = "",
        phone: str = "",
        category: str = "bd",
        category_label: str = "",
        next_action: str = "",
        next_due: str = "",
        notes: str = "",
    ) -> dict:
        """Create a contact on the current user's board. Only `name` is required.
        category is one of: bd, gc, vendor, property, client, sub, designer,
        insurance, other (use 'other' + category_label for anything else).
        next_due is an ISO date (YYYY-MM-DD). If the user hasn't mentioned notes
        or a follow-up date, ASK before saving rather than leaving them blank.
        Run search_contacts first; if a likely match exists, confirm before adding."""
        db = SessionLocal()
        try:
            return op_create(
                db, _resolve_user(db), name=name, company=company, role=role,
                email=email, phone=phone, category=category,
                category_label=category_label, next_action=next_action,
                next_due=next_due, notes=notes,
            )
        finally:
            db.close()

    @mcp.tool()
    def update_contact(
        contact_id: str,
        name: Optional[str] = None,
        company: Optional[str] = None,
        role: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        category: Optional[str] = None,
        category_label: Optional[str] = None,
        next_action: Optional[str] = None,
        next_due: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """Update fields on an existing contact. Only pass the fields you want to
        change; omitted fields are left as-is."""
        db = SessionLocal()
        try:
            return op_update(
                db, _resolve_user(db), contact_id, name=name, company=company,
                role=role, email=email, phone=phone, category=category,
                category_label=category_label, next_action=next_action,
                next_due=next_due, notes=notes,
            )
        finally:
            db.close()

    @mcp.tool()
    def delete_contact(contact_id: str) -> dict:
        """Permanently delete a contact. Destructive — only call after the user
        has explicitly confirmed they want this exact contact removed."""
        db = SessionLocal()
        try:
            return op_delete(db, _resolve_user(db), contact_id)
        finally:
            db.close()

    @mcp.tool()
    def log_touch(contact_id: str, note: str, date: str = "") -> dict:
        """Add an activity-log entry (a 'touch') to a contact. date is an ISO
        date (YYYY-MM-DD); defaults to today if omitted."""
        db = SessionLocal()
        try:
            return op_log_touch(db, _resolve_user(db), contact_id, note, date)
        finally:
            db.close()

    app = mcp.streamable_http_app()

    # Override the protected-resource metadata's `resource` field to the Entra App
    # ID URI (api://<client-id>) instead of the HTTP MCP URL. Claude echoes this
    # value as the RFC 8707 `resource` parameter at Entra's token endpoint, and
    # Entra only accepts a registered App ID URI there. The HTTP URL fails with
    # AADSTS9010010 / invalid_target, and it can't be registered as an identifier
    # URI because run.app is not a verified domain of the tenant. authorization_
    # servers still points at our origin (the AS-metadata shim in main.py).
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    _prm = {
        "resource": f"api://{auth.CLIENT_ID}",
        "authorization_servers": [origin.rstrip("/") + "/"],
        "scopes_supported": [f"api://{auth.CLIENT_ID}/access_as_user"],
        "bearer_methods_supported": ["header"],
    }

    async def _protected_resource_metadata(request):  # noqa: ANN001
        return JSONResponse(_prm)

    for _i, _route in enumerate(app.routes):
        if getattr(_route, "path", "") == "/.well-known/oauth-protected-resource":
            app.routes[_i] = Route(
                "/.well-known/oauth-protected-resource",
                endpoint=_protected_resource_metadata,
                methods=["GET", "OPTIONS"],
            )
            break

    return app
