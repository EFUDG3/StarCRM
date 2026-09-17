"""Star CRM API — proof of concept backend.

Multi-user with NO authentication. A "user" is just a profile that owns its own
contacts, selected client-side and passed via the X-User-Id header. When SSO
(Entra ID / Microsoft Graph) lands, only how the current user is identified
changes — the per-user data model here stays the same. Do not expose publicly
until that auth layer exists.

Run locally:
    uvicorn main:app --reload
"""
import json
import os
from contextlib import asynccontextmanager
from datetime import date as date_cls, datetime
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import accounts
import admin
import chat_feedback
import digest
import auth
import cards
import chat
import email_triage
import m365
import mileage
import models  # noqa: F401 (ensures models are registered on Base)
import projects
import tasks
import telemetry
import triage_feedback
import user_files
from database import DATABASE_URL, Base, engine, get_db
from models import ChatFeedback, ChatPreference, ChatSuggestion, Contact, Interaction, SystemPromptSection, User
from schemas import ChatIn, ContactIn, LogEditIn, LogIn, TodoIn, TodoPatch, UserIn


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
        # Multiple typed phone numbers per contact (cell/work/home). Additive;
        # existing single `phone` values are surfaced as a one-entry list by
        # serialize() until the contact is next edited.
        conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS phones_json TEXT"))
        # Optional postal address (most business cards have one). Additive;
        # shown on the detail view only, deliberately left off the list table.
        conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS address TEXT"))
        # Log-note attribution + edit/soft-delete (mirrors account_interactions.by,
        # since the profile switcher means a note's author isn't always the
        # contact's owner). Additive; existing notes get by='' (pre-dates this).
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS by VARCHAR DEFAULT ''"))
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()"))
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS edited_by VARCHAR"))
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS deleted_by VARCHAR"))
        conn.execute(text("ALTER TABLE interactions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))
        # Card-image storage removed 2026-07 (space + UI cleanup; scans still
        # prefill contacts, the photo just isn't kept). Blobs were exported to
        # ~/Documents/starbot-card-image-backup before this shipped.
        conn.execute(text("ALTER TABLE contacts DROP COLUMN IF EXISTS card_image"))
        conn.execute(text("ALTER TABLE contacts DROP COLUMN IF EXISTS card_image_type"))
        # Email dismiss columns on email_threads (additive).
        conn.execute(text("ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS dismissed_at_msg_id VARCHAR DEFAULT ''"))
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
        # Mileage: per-trip reimbursement rate (entry-time choice). Additive;
        # pre-existing trips backfill to the IRS standard.
        conn.execute(text("ALTER TABLE trips ADD COLUMN IF NOT EXISTS rate DOUBLE PRECISION"))
        conn.execute(text("UPDATE trips SET rate = 0.70 WHERE rate IS NULL"))
        # Mileage: calendar scans day-stamp addresses into place_visits (table
        # itself comes from create_all). One row per user+place+date — the
        # index makes re-scans idempotent even under concurrent requests.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_place_visits_user_place_date "
            "ON place_visits (user_id, place_id, date)"
        ))
        # Tasks tab: one dismissal per user+flagged-email; the unique index keeps
        # a double-dismiss idempotent.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_dismissed_user_msg "
            "ON dismissed_emails (user_id, message_id)"
        ))
        # Email triage feedback loop (2026-09-02). The signal set + deciding
        # rule are stored per thread so a correction can be replayed against
        # the facts the engine actually saw; without them every correction is
        # a guess about which of a dozen rules misfired.
        conn.execute(text("ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS signals_json TEXT DEFAULT '{}'"))
        conn.execute(text("ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS decided_by VARCHAR DEFAULT ''"))
        conn.execute(text("ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS manual_msg_id VARCHAR DEFAULT ''"))
        # Classic Outlook desktop deep links (2026-09-14). Hex MAPI EntryID per
        # thread + the per-user opt-in. Both additive; empty/false is the
        # correct degraded state for everyone without the registry key.
        conn.execute(text("ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS entry_id_hex VARCHAR DEFAULT ''"))
        conn.execute(text("ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS open_in_desktop BOOLEAN NOT NULL DEFAULT FALSE"))
        # Per-user rule scoping. NULL triage_rules_json means "not initialized"
        # -> tier-1 mechanical rules only, so a new inbox never inherits
        # another person's judgment rules.
        conn.execute(text("ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS triage_rules_json TEXT"))
        conn.execute(text("ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS triage_profile TEXT DEFAULT ''"))
        # One open suggestion per user+pattern: keeps the suggester from
        # proposing the same rule twice while an earlier card is still pending.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_triage_sugg_user_pattern "
            "ON triage_suggestions (user_id, scope, pattern, action)"
        ))
        # Admin flag on users (2026-09-15). Additive; defaults to FALSE.
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
        # Set ethan as admin by email (idempotent).
        conn.execute(text(
            "UPDATE users SET is_admin = TRUE "
            "WHERE email = 'ethan@starflooringandremodeling.com' AND is_admin = FALSE"
        ))
        # Chat feedback + per-user preferences (Phase 2, 2026-09-15).
        # Tables are created by create_all above; indexes are additive.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_chat_sugg_user_cat_text "
            "ON chat_suggestions (user_id, category, text)"
        ))
        # User file uploads (Phase 3, 2026-09-15).
        # Table created by create_all; index for the list-by-user query.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_user_files_user_uploaded "
            "ON user_files (user_id, uploaded_at DESC)"
        ))


def _ensure_schema_or_explain() -> None:
    """Run the schema migration, turning an unreachable database into a message
    that says what to do about it.

    _ensure_schema() used to run at MODULE scope, which meant the very first
    thing an import did was open a database connection. When the database was
    unreachable that import blocked, so uvicorn never bound its port and never
    logged "Application startup complete": no traceback, no error, just a
    process that appeared to hang after "Started reloader process". Running it
    here (from the lifespan) lets the server bind first and fail loudly.
    """
    try:
        _ensure_schema()
    except OperationalError as e:
        host = make_url(DATABASE_URL).host or "(unknown host)"
        raise RuntimeError(
            f"Cannot reach the database at {host}. The app cannot start.\n"
            "Locally, the usual cause is that your public IP changed and is no "
            "longer in the Azure Postgres firewall: Portal -> star-crm-pg -> "
            "Networking -> '+ Add current client IP address' -> Save.\n"
            "Other candidates: the server is stopped/paused, DATABASE_URL is "
            "wrong, or the password was rotated.\n"
            f"Underlying error: {e.orig}"
        ) from e


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
    """Startup/shutdown. Applies the additive schema migration, seeds
    company-wide data, runs the one-time notes-to-log-note backfill and, when
    the connector is mounted, runs the MCP session manager's lifespan. Schema
    work happens HERE rather than at import so an unreachable database is a
    startup error, not a silent hang."""
    _ensure_schema_or_explain()
    _seed_on_first_run()
    _migrate_notes_to_log()
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


def _contact_phones(c: Contact) -> list:
    """Parse a contact's phones_json into [{type, number}]; fall back to the
    legacy single `phone` so older rows still surface a number."""
    phones = []
    try:
        for p in json.loads(c.phones_json or "[]"):
            if isinstance(p, dict) and str(p.get("number", "")).strip():
                phones.append({
                    "type": str(p.get("type", "") or "").strip(),
                    "number": str(p.get("number")).strip(),
                })
    except Exception:
        phones = []
    if not phones and (c.phone or "").strip():
        phones.append({"type": "", "number": c.phone.strip()})
    return phones


def serialize(c: Contact) -> dict:
    """Return a contact in the exact shape the React frontend expects.

    Interactions are sorted newest-first (by date, then insert order within a
    day) so the activity log reads top-down. Soft-deleted notes are split into
    their own `deletedLog` list rather than dropped, for auditing.
    """
    active = [i for i in c.interactions if not i.deleted]
    removed = [i for i in c.interactions if i.deleted]
    log = sorted(active, key=lambda i: (i.date, i.created_at or datetime.min), reverse=True)
    deleted_log = sorted(removed, key=lambda i: i.deleted_at or datetime.min, reverse=True)
    phones = _contact_phones(c)
    return {
        "id": c.id,
        "name": c.name,
        "company": c.company or "",
        "role": c.role or "",
        "email": c.email or "",
        "phone": phones[0]["number"] if phones else (c.phone or ""),
        "phones": phones,
        "address": c.address or "",
        "category": c.category or "bd",
        "categoryLabel": c.category_label or "",
        "nextAction": c.next_action or "",
        "nextDue": c.next_due or "",
        "created": c.created_at.date().isoformat() if c.created_at else "",
        "createdAt": c.created_at.isoformat() if c.created_at else "",  # full timestamp, for precise newest-first sort
        "log": [
            {
                "id": i.id, "date": i.date, "note": i.note, "by": i.by or "",
                "editedBy": i.edited_by or "", "editedAt": i.edited_at.isoformat() if i.edited_at else "",
            }
            for i in log
        ],
        "deletedLog": [
            {
                "id": i.id, "date": i.date, "note": i.note, "by": i.by or "",
                "deletedBy": i.deleted_by or "", "deletedAt": i.deleted_at.isoformat() if i.deleted_at else "",
            }
            for i in deleted_log
        ],
    }


def _clean_phones_payload(payload) -> tuple[str, str]:
    """From a ContactIn, return (phones_json, primary_number). Accepts the new
    `phones` list of {type, number}; falls back to the legacy single `phone`."""
    phones = []
    for p in (getattr(payload, "phones", None) or []):
        if isinstance(p, dict):
            num = str(p.get("number", "") or "").strip()
            typ = str(p.get("type", "") or "").strip().lower()
        else:
            num, typ = str(p).strip(), ""
        if num:
            phones.append({"type": typ, "number": num[:40]})
    if not phones and (payload.phone or "").strip():
        phones.append({"type": "", "number": payload.phone.strip()[:40]})
    phones = phones[:8]
    return json.dumps(phones), (phones[0]["number"] if phones else "")


def get_current_user(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    starbot_session: str | None = Cookie(default=None, alias=m365.SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the active user. Three doors, in order:

    1. Entra Bearer token — the Claude MCP connector.
    2. Microsoft session cookie — the browser. This is the COMPANY GATE: once
       M365 login is configured, nobody reaches CRM data without signing in
       with a Star account. A signed-in user may still pass X-User-Id to view
       another profile's board (the profile switcher).
    3. Bare X-User-Id — dev fallback, only when M365 login isn't configured.
    """
    if auth.ENTRA_ENABLED and authorization and authorization.lower().startswith("bearer "):
        claims = auth.validate_entra_token(authorization.split(" ", 1)[1])
        return auth.user_from_claims(claims, db)
    if m365.M365_LOGIN_ENABLED:
        session_user = m365.get_session_user(starbot_session, db)  # 401 if not signed in
        if x_user_id:
            user = db.get(User, x_user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            return user
        return session_user
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


def _seed_prompt_sections(db: Session) -> None:
    """Seed the system prompt sections from the original hardcoded content.
    Only runs when the table is empty (first boot after this feature lands)."""
    if db.query(SystemPromptSection).first() is not None:
        return

    _SEED_SECTIONS = [
        (
            "identity",
            "Identity",
            (
                "You are starbot, the internal AI assistant for Star Flooring & Remodeling "
                "(San Diego flooring, remodeling, and flood-restoration company). You are "
                "talking to {user_name} ({user_email}). Today is {weekday}, {today} "
                "(Pacific time)."
            ),
            0,
        ),
        (
            "capabilities",
            "Capabilities",
            (
                "You can read the user's Outlook email and calendar, search company "
                "SharePoint/OneDrive files, manage their starbot to-do list, and look up "
                "their Star CRM contacts — those are all PRIVATE to this signed-in user. "
                "You can also see the STAR MAIL ranking of this user's own inbox "
                "(list_ranked_emails / get_email_thread) — that is the same triage the "
                "Email tab shows, and it is the best way to answer 'what needs my reply' "
                "without dumping the whole mailbox. You can also read and update two "
                "SHARED team boards that everyone at Star sees: ACCOUNTS, the sales hit "
                "list of about 120 property-management companies, and PROJECTS, the PM "
                "team's large commercial construction jobs such as schools and restaurants. "
                "You can also search the web for current or general information that isn't "
                "in Star's own data."
            ),
            10,
        ),
        (
            "rules",
            "Rules",
            (
                "Rules:\n"
                "- CITE SOURCES. When an answer draws on an email, event, or file, cite "
                "it inline as a markdown link, e.g. [RE: 4620 walkthrough](webLink) — use "
                "each item's webLink. Never invent a link or a fact you didn't read from a "
                "tool result.\n"
                "- WEB SEARCH: use it for external facts the user's M365 data can't "
                "answer — current events, prices, product specs, codes/regulations, vendor "
                "lookups, general reference. Cite web results inline as markdown links to "
                "the source URL, same as any other source. Prefer the user's own "
                "email/calendar/files/CRM for anything internal; reach for the web only "
                "when the answer lives outside Star's data. Don't search for things you "
                "already know confidently unless the user wants current or verified info.\n"
                "- SHARED BOARDS ARE TEAM-VISIBLE, so treat writes carefully. Anything you "
                "log or change on Accounts or Projects is seen and trusted by the whole "
                "company, and there is no undo in chat. Search first, make sure you have "
                "the RIGHT record when a name is ambiguous (ask if two could match), and "
                "only write when the user clearly asked you to. Never invent a contact, a "
                "date, or a job detail. You CANNOT create or delete accounts or projects "
                "from chat by design — for those, point the user at the Accounts or "
                "Projects tab.\n"
                "- KEEP THE TWO BOARDS STRAIGHT. Accounts is SALES prospecting: "
                "property-management companies, an assigned rep, unit and property counts, "
                "a pipeline status. Projects is the PM team's CONSTRUCTION work: a job "
                "name, the GC or owner as client, a stage, a project manager, target "
                "completion. They are separate boards with no link between them, so a "
                "school or restaurant job is a PROJECT, not an account. For either board, "
                "use search_accounts or list_projects to get the overview, then "
                "get_account or get_project for one record's full history — do not pull "
                "every record to answer a narrow question.\n"
                "- To-do requests (\"make a to-do list from my emails\"): call list_todos "
                "first, then scan list_recent_emails (and the calendar when relevant), "
                "propose clear action items with due dates when the email implies one, and "
                "add them with add_todos including source + source_link, plus a priority "
                "when urgency is clear. Skip anything already on the list; say so briefly. "
                "Tasks live on the user's Tasks board (columns: To do / In progress / "
                "Done) — use update_todo to move or re-prioritize them when asked.\n"
                "- BE FRUGAL WITH FULL EMAIL READS. The preview from list/search results "
                "is usually enough to triage or extract a to-do. Call read_email only when "
                "the decision truly needs the body (e.g. drafting a reply, a specific "
                "detail the user asked for), at most 2-3 per request, prioritized. Never "
                "read every email in a list.\n"
                "- Inbox triage (\"organize/triage my inbox\"): group into **Action "
                "needed**, **Waiting / follow-up**, **FYI — no action**, and **Junk / can "
                "ignore**, newest first, each item as '[subject](webLink) — sender, date: "
                "one-line why'. Recommend, don't nag.\n"
                "- Email drafting: when asked to draft a reply, read the thread first, "
                "then write the draft in a fenced block ready to copy. Match a "
                "professional, direct tone; sign as {user_first_name}. You cannot send "
                "email — say the draft is ready to copy into Outlook.\n"
                "- Be concise and skimmable: short paragraphs, bullets for lists, bold "
                "for the load-bearing bits. No filler, no restating the question.\n"
                "- If a tool errors with a sign-in problem, tell the user to sign in "
                "again via the chat tab. If you lack a capability (sending mail, editing "
                "files), say so plainly."
            ),
            20,
        ),
    ]
    for key, label, content, order in _SEED_SECTIONS:
        db.add(SystemPromptSection(
            key=key, label=label, content=content, sort_order=order,
        ))
    db.commit()


def _seed_on_first_run() -> None:
    """Seed company-wide, user-independent data on startup. Every real user
    profile now comes from M365 sign-in (auth.user_from_claims creates one on
    first use) — no demo user or demo contacts are seeded here anymore; a new
    profile just starts blank. Called from the lifespan handler above."""
    db = next(get_db())
    try:
        # Shared accounts seed their own 5 pilot rows (independent of users —
        # self-guards on an empty accounts table, so it runs once).
        accounts.seed_accounts(db)
        # Triage glossary (email_triage) seeds itself the same way.
        email_triage.seed_glossary(db)
        # System prompt sections seed from the original hardcoded content.
        _seed_prompt_sections(db)
    finally:
        db.close()


def _migrate_notes_to_log() -> None:
    """One-time backfill (2026-09): the freeform `notes` field let anyone
    bypass the interaction-log audit trail entirely — no attribution, no
    timestamp, no edit history, just overwrite it via the edit form — so it's
    retired from the API/UI in favor of log notes (which support edit/delete,
    both attributed). Existing content is preserved as a single legacy log
    note per contact rather than lost.

    Attribution for anything that predates real attribution (migrated notes,
    and any log note written before the `by` column existed) falls back to
    that CONTACT'S OWNER, not a generic placeholder — Ethan's call: this is
    real historical data with very few entries, all written by (or in front
    of) the board's own owner before the switcher saw meaningful use, so it's
    not an auditing gap worth flagging — just unattractive to a non-technical
    viewer as "unknown". Re-running this also fixes any row already stamped
    with the earlier "(imported from Notes field)" placeholder.

    Idempotent: only touches contacts whose `notes` is still non-empty
    (clearing it after migrating) and interactions whose `by` is still blank
    or the old placeholder, so this is a no-op on every boot after the first
    full pass. The `notes` column itself is NOT dropped (this project never
    drops columns) — it's just permanently blank going forward.
    """
    db = next(get_db())
    try:
        rows = db.query(Contact).filter(Contact.notes.isnot(None), Contact.notes != "").all()
        for c in rows:
            c.interactions.append(
                Interaction(
                    date=c.created_at.date().isoformat() if c.created_at else today_iso(),
                    note=c.notes,
                    by=c.owner.name if c.owner else "",
                )
            )
            c.notes = ""
        if rows:
            db.commit()

        unattributed = [
            i for i in db.query(Interaction).all()
            if not (i.by or "").strip() or i.by == "(imported from Notes field)"
        ]
        for i in unattributed:
            if i.contact and i.contact.owner:
                i.by = i.contact.owner.name
        if unattributed:
            db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/stats")
def usage_stats(
    days: int = 30,
    _viewer: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregate usage telemetry (counts only, no content) for the value
    report. Any signed-in employee may view it."""
    return telemetry.compute_stats(db, days)


# --- Starbot: M365 sign-in, chat, to-dos ------------------------------------
# These routes use the M365 session cookie (m365.get_session_user), NOT the
# X-User-Id header — the chat needs Graph tokens, which hang off the Microsoft
# identity. The CRM board's existing routes are unchanged.
app.include_router(m365.router)
app.include_router(mileage.router)
app.include_router(accounts.router)
app.include_router(projects.router)
app.include_router(email_triage.router)
app.include_router(triage_feedback.router)
app.include_router(digest.router)
app.include_router(tasks.router)
app.include_router(admin.router)
app.include_router(chat_feedback.router)
app.include_router(user_files.router)


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
    telemetry.log_event(user.id, "chat", "message")
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
    telemetry.log_event(user.id, "tasks", "todo_created")
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
        updated = chat.op_update_todo(db, user, todo_id, **fields)
        telemetry.log_event(user.id, "tasks", "todo_updated", ",".join(sorted(fields)))
        return updated
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
        telemetry.log_event(user.id, "tasks", "todo_deleted")
    except ValueError:
        raise HTTPException(status_code=404, detail="To-do not found")


# --- Users (selectable profiles; gated behind the company sign-in) ----------
@app.get("/api/users")
def list_users(
    _viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list:
    users = db.query(User).order_by(User.created_at).all()
    return [{"id": u.id, "name": u.name} for u in users]


@app.post("/api/users", status_code=201)
def create_user(
    payload: UserIn,
    _viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    user = User(name=name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "name": user.name}


@app.patch("/api/users/{user_id}")
def rename_user(
    user_id: str,
    payload: UserIn,
    _viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
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
def delete_user(
    user_id: str,
    _viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
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
    actor_name: str | None = Depends(m365.get_actor_name),
    db: Session = Depends(get_db),
) -> dict:
    phones_json, primary = _clean_phones_payload(payload)
    contact = Contact(
        user_id=user.id,
        name=payload.name,
        company=payload.company,
        role=payload.role,
        email=payload.email,
        phone=primary,
        phones_json=phones_json,
        address=payload.address,
        category=payload.category,
        category_label=payload.categoryLabel,
        next_action=payload.nextAction,
        next_due=payload.nextDue,
    )
    note = payload.note.strip()
    if note:
        contact.interactions.append(Interaction(date=today_iso(), note=note, by=actor_name or user.name))
    db.add(contact)
    db.commit()
    db.refresh(contact)
    telemetry.log_event(user.id, "crm", "contact_created")
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
    phones_json, primary = _clean_phones_payload(payload)
    contact.name = payload.name
    contact.company = payload.company
    contact.role = payload.role
    contact.email = payload.email
    contact.phone = primary
    contact.phones_json = phones_json
    contact.address = payload.address
    contact.category = payload.category
    contact.category_label = payload.categoryLabel
    contact.next_action = payload.nextAction
    contact.next_due = payload.nextDue
    db.commit()
    db.refresh(contact)
    telemetry.log_event(user.id, "crm", "contact_updated")
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
    telemetry.log_event(user.id, "crm", "contact_deleted")


@app.post("/api/contacts/{contact_id}/log")
def log_touch(
    contact_id: str,
    payload: LogIn,
    user: User = Depends(get_current_user),
    actor_name: str | None = Depends(m365.get_actor_name),
    db: Session = Depends(get_db),
) -> dict:
    contact = _get_or_404(db, user, contact_id)
    contact.interactions.append(
        Interaction(date=payload.date or today_iso(), note=payload.note, by=actor_name or user.name)
    )
    db.commit()
    db.refresh(contact)
    telemetry.log_event(user.id, "crm", "touch_logged")
    return serialize(contact)


def _get_interaction_or_404(contact: Contact, interaction_id: str) -> Interaction:
    interaction = next(
        (i for i in contact.interactions if i.id == interaction_id and not i.deleted), None
    )
    if interaction is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return interaction


@app.put("/api/contacts/{contact_id}/log/{interaction_id}")
def edit_log(
    contact_id: str,
    interaction_id: str,
    payload: LogEditIn,
    user: User = Depends(get_current_user),
    actor_name: str | None = Depends(m365.get_actor_name),
    db: Session = Depends(get_db),
) -> dict:
    """Edit a note's text — e.g. fixing a typo shouldn't require deleting the
    original and writing a whole new one. Stamps who made the edit; the
    original author (`by`) is left alone."""
    contact = _get_or_404(db, user, contact_id)
    interaction = _get_interaction_or_404(contact, interaction_id)
    note = payload.note.strip()
    if not note:
        raise HTTPException(status_code=400, detail="Note is required")
    interaction.note = note
    interaction.edited_by = actor_name or user.name
    interaction.edited_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)
    telemetry.log_event(user.id, "crm", "note_edited")
    return serialize(contact)


@app.delete("/api/contacts/{contact_id}/log/{interaction_id}")
def delete_log(
    contact_id: str,
    interaction_id: str,
    user: User = Depends(get_current_user),
    actor_name: str | None = Depends(m365.get_actor_name),
    db: Session = Depends(get_db),
) -> dict:
    """Soft-delete a note — it moves to `deletedLog` rather than vanishing, so
    there's an audit trail of who removed what."""
    contact = _get_or_404(db, user, contact_id)
    interaction = _get_interaction_or_404(contact, interaction_id)
    interaction.deleted = True
    interaction.deleted_by = actor_name or user.name
    interaction.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)
    telemetry.log_event(user.id, "crm", "note_deleted")
    return serialize(contact)


@app.post("/api/contacts/{contact_id}/complete")
def complete_action(
    contact_id: str,
    user: User = Depends(get_current_user),
    actor_name: str | None = Depends(m365.get_actor_name),
    db: Session = Depends(get_db),
) -> dict:
    """Log the current next action as done and clear it."""
    contact = _get_or_404(db, user, contact_id)
    done_note = "Done: " + (contact.next_action or "follow-up")
    contact.interactions.append(Interaction(date=today_iso(), note=done_note, by=actor_name or user.name))
    contact.next_action = ""
    contact.next_due = ""
    db.commit()
    db.refresh(contact)
    telemetry.log_event(user.id, "crm", "action_completed")
    return serialize(contact)


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
    telemetry.log_event(user.id, "crm", "card_scanned")
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

    _MCP_RESOURCE = os.getenv("MCP_RESOURCE_URL", "http://localhost:8000/mcp")
    _AS_ORIGIN = _MCP_RESOURCE.rsplit("/mcp", 1)[0]
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
            f"{_MCP_RESOURCE}/access_as_user",
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
