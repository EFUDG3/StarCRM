"""Shared 'hit list' Accounts — company-wide sales prospecting records.

Unlike Contacts (private per profile, X-User-Id scoped), Accounts are a shared
TEAM asset: any signed-in Star user sees and edits every account. `rep` marks
who owns the relationship (blank = unassigned); `updated_by`/`updated_at` track
who touched it last so a cold account is easy to spot. Addresses and emails are
JSON string lists (one account, several of each); customer people live in
account_contacts and are replaced wholesale from the edit form on each save.

Seeded on first run with 5 real Hit List II (management-company) rows for the
pilot; the full ~120-row import lands after the source data is cleaned.
"""
import json
from datetime import date as date_cls, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

import m365
import telemetry
from database import get_db
from models import Account, AccountContact, AccountInteraction, User
from schemas import AccountIn, AccountLogIn, _STATUSES

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today() -> str:
    return date_cls.today().isoformat()


def _clean_list(raw) -> list:
    """Coerce a value into a list of non-empty trimmed strings."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def serialize(a: Account) -> dict:
    """Shape one account for the frontend (JSON lists parsed back to arrays)."""
    contacts = sorted(a.contacts, key=lambda c: (c.created_at or datetime.min))
    # Activity log newest-first (by date, then insert order within a day).
    log = sorted(
        a.interactions,
        key=lambda i: (i.date, i.created_at or datetime.min),
        reverse=True,
    )
    return {
        "id": a.id,
        "name": a.name,
        "rep": a.rep or "",
        "status": a.status or "prospect",
        "website": a.website or "",
        "phone": a.phone or "",
        "addresses": _clean_list(a.addresses_json),
        "emails": _clean_list(a.emails_json),
        "numProperties": a.num_properties,
        "totalUnits": a.total_units,
        "notes": a.notes or "",
        "created": a.created_at.date().isoformat() if a.created_at else "",
        "updated": a.updated_at.isoformat() if a.updated_at else "",
        "updatedBy": a.updated_by or "",
        "contacts": [
            {
                "id": c.id,
                "name": c.name,
                "role": c.role or "",
                "email": c.email or "",
                "phone": c.phone or "",
                "address": c.address or "",
            }
            for c in contacts
        ],
        "log": [
            {"date": i.date, "note": i.note, "by": i.by or ""}
            for i in log
        ],
    }


def _apply(a: Account, payload: AccountIn, user: User) -> None:
    """Copy a validated payload onto an account, stamp the editor, and replace
    the contact list from the form (the form owns the whole list)."""
    a.name = payload.name.strip()
    a.rep = (payload.rep or "").strip()
    a.status = (payload.status or "prospect").strip() or "prospect"
    a.website = (payload.website or "").strip()
    a.phone = (payload.phone or "").strip()
    a.addresses_json = json.dumps(_clean_list(payload.addresses))
    a.emails_json = json.dumps(_clean_list(payload.emails))
    a.num_properties = payload.numProperties
    a.total_units = payload.totalUnits
    a.notes = payload.notes or ""
    a.updated_at = _now()
    a.updated_by = user.name

    a.contacts.clear()  # delete-orphan removes the old rows on flush
    for c in (payload.contacts or []):
        name = (c.name or "").strip()
        if not name:  # skip blank rows the form may send
            continue
        a.contacts.append(
            AccountContact(
                name=name,
                role=(c.role or "").strip(),
                email=(c.email or "").strip(),
                phone=(c.phone or "").strip(),
                address=(c.address or "").strip(),
            )
        )


@router.get("")
def list_accounts(
    _user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> list:
    """Every account, for everyone. Frontend does the ranking/filtering.

    Eager-loads contacts + interactions: serialize() touches both on every row,
    so lazy loading meant 2 extra round-trips PER account (241 queries for 120
    accounts, ~29s from off-Azure). selectinload makes it 3 queries flat."""
    rows = (
        db.query(Account)
        .options(selectinload(Account.contacts), selectinload(Account.interactions))
        .all()
    )
    return [serialize(a) for a in rows]


@router.post("", status_code=201)
def create_account(
    payload: AccountIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Account name is required")
    a = Account()
    _apply(a, payload, user)
    db.add(a)
    db.commit()
    db.refresh(a)
    telemetry.log_event(user.id, "accounts", "account_created")
    return serialize(a)


@router.get("/{account_id}")
def get_account(
    account_id: str,
    _user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return serialize(a)


@router.put("/{account_id}")
def update_account(
    account_id: str,
    payload: AccountIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Account name is required")
    _apply(a, payload, user)
    db.commit()
    db.refresh(a)
    telemetry.log_event(user.id, "accounts", "account_updated")
    return serialize(a)


@router.post("/{account_id}/log")
def log_note(
    account_id: str,
    payload: AccountLogIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Append a dated note to the account's activity log (the CRM-style 'log
    note' history). Stamps who logged it and touches updated_at/by."""
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Account not found")
    note = payload.note.strip()
    if not note:
        raise HTTPException(status_code=400, detail="Note is required")
    a.interactions.append(
        AccountInteraction(date=(payload.date or _today()), note=note, by=user.name)
    )
    a.updated_at = _now()
    a.updated_by = user.name
    db.commit()
    db.refresh(a)
    telemetry.log_event(user.id, "accounts", "note_logged")
    return serialize(a)


@router.delete("/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(a)  # account_contacts cascade
    db.commit()
    telemetry.log_event(user.id, "accounts", "account_deleted")


# --- Operations for starbot chat (and, later, the Claude connector) ----------
#
# These back the chat's account tools. Two deliberate differences from the CRM
# ops in mcp_server.py:
#
#   1. NO user scoping. Accounts are a shared company-wide asset, so every
#      signed-in user legitimately sees every account (same as the tab).
#   2. Search returns COMPACT rows, not serialize(). serialize() carries every
#      contact and every log line; 120 of those would dump the whole hit list
#      into the model's context and blow the input budget. The model gets a
#      summary row plus an id, and calls op_account_get for the one it needs.
#
# Write surface is intentionally narrow: log a note and set status. NO create
# and NO delete from chat — this table is shared, and duplicate rows created by
# a chatty model are exactly the mess that took a manual cleanup pass in Aug.

_ACCT_SEARCH_DEFAULT = 15
_ACCT_SEARCH_MAX = 40


def _compact(a: Account) -> dict:
    """One account as a summary row (no contacts/log bodies)."""
    addrs = _clean_list(a.addresses_json)
    return {
        "id": a.id,
        "name": a.name,
        "rep": a.rep or "",
        "status": a.status or "prospect",
        "phone": a.phone or "",
        "address": addrs[0] if addrs else "",
        "numProperties": a.num_properties,
        "totalUnits": a.total_units,
        "contactCount": len(a.contacts),
        "lastActivity": max((i.date for i in a.interactions), default=""),
    }


def _haystack(a: Account) -> str:
    parts = [a.name, a.rep or "", a.notes or "", a.status or ""]
    parts += _clean_list(a.addresses_json) + _clean_list(a.emails_json)
    parts += [f"{c.name} {c.role or ''} {c.email or ''}" for c in a.contacts]
    return " ".join(parts).lower()


def _all_accounts(db: Session) -> list[Account]:
    # Eager-load both relationships: _compact reads contacts + interactions on
    # every row, so lazy loading would be 2 extra queries per account.
    return (
        db.query(Account)
        .options(selectinload(Account.contacts), selectinload(Account.interactions))
        .all()
    )


def op_account_search(
    db: Session, query: str = "", rep: str = "", status: str = "", limit: int = 0
) -> dict:
    """Search the shared hit list. Ranked by unit count (biggest opportunity
    first), same as the Accounts tab. Reports how many matched vs. returned so
    the model knows when it is looking at a truncated list."""
    rows = _all_accounts(db)
    total = len(rows)

    q = (query or "").strip().lower()
    if q:
        rows = [a for a in rows if q in _haystack(a)]
    r = (rep or "").strip().lower()
    if r:
        # Reps come in as "Rudy" or "Salam / Leighann" — match any token.
        rows = [
            a for a in rows
            if any(t.strip().startswith(r) for t in (a.rep or "").lower().split("/"))
        ]
    st = (status or "").strip().lower()
    if st:
        rows = [a for a in rows if (a.status or "prospect").lower() == st]

    matched = len(rows)
    rows.sort(key=lambda a: (a.total_units is None, -(a.total_units or 0)))
    cap = max(1, min(int(limit or _ACCT_SEARCH_DEFAULT), _ACCT_SEARCH_MAX))
    out = rows[:cap]
    return {
        "totalAccounts": total,
        "matched": matched,
        "returned": len(out),
        "truncated": matched > len(out),
        "accounts": [_compact(a) for a in out],
    }


def op_account_get(db: Session, account_id: str) -> dict:
    """One account in full — contacts and the whole activity log."""
    a = db.get(Account, account_id)
    if a is None:
        return {"error": f"No account with id {account_id}"}
    return serialize(a)


def op_account_log(
    db: Session, user: User, account_id: str, note: str, date: str | None = None
) -> dict:
    """Append a dated note to a shared account's activity log, stamped with
    who logged it (mirrors POST /api/accounts/{id}/log)."""
    a = db.get(Account, account_id)
    if a is None:
        return {"error": f"No account with id {account_id}"}
    text = (note or "").strip()
    if not text:
        return {"error": "note is required"}
    a.interactions.append(
        AccountInteraction(date=(date or _today()), note=text[:2000], by=user.name)
    )
    a.updated_at = _now()
    a.updated_by = user.name
    db.commit()
    db.refresh(a)
    telemetry.log_event(user.id, "accounts", "note_logged", "chat")
    return serialize(a)


def op_account_set_status(db: Session, user: User, account_id: str, status: str) -> dict:
    """Move an account along the pipeline. Rejects unknown values rather than
    silently coercing, so the model is told when it guessed wrong (the HTTP
    layer coerces instead, because a form should never hard-fail)."""
    a = db.get(Account, account_id)
    if a is None:
        return {"error": f"No account with id {account_id}"}
    st = (status or "").strip().lower()
    if st not in _STATUSES:
        return {"error": f"Unknown status '{status}'. Valid: {sorted(_STATUSES)}"}
    a.status = st
    a.updated_at = _now()
    a.updated_by = user.name
    db.commit()
    db.refresh(a)
    telemetry.log_event(user.id, "accounts", "account_updated", "chat:status")
    return _compact(a)


# --- First-run seed: 5 real Hit List II rows for the pilot ------------------
SEED_ACCOUNTS = [
    {
        "name": "CONAM Management Corporation", "rep": "RJ", "status": "prospect",
        "website": "http://www.conam.com", "phone": "(858) 614-7200",
        "addresses": ["9201 Spectrum Center Blvd, Suite 200, San Diego, CA 92123"],
        "emails": [], "numProperties": 91, "totalUnits": 10744,
        "notes": "Largest target on the list — 91 properties.", "contacts": [],
    },
    {
        "name": "R.A. Snyder Properties, Inc.", "rep": "Rudy", "status": "active",
        "website": "http://www.rasnyder.com", "phone": "(619) 297-0274",
        "addresses": ["2399 Camino Del Rio S, Suite 102, San Diego, CA 92108"],
        "emails": ["belinda@rasnyder.com", "smojica@rasnyder.com"],
        "numProperties": 32, "totalUnits": 3733,
        "notes": "Active account. AP contact Belinda; coordinator S. Mojica.",
        "contacts": [
            {"name": "Belinda Torres", "role": "AP / Billing", "email": "belinda@rasnyder.com", "phone": "(619) 297-0274", "address": ""},
            {"name": "S. Mojica", "role": "Coordinator", "email": "smojica@rasnyder.com", "phone": "", "address": ""},
        ],
    },
    {
        "name": "Fairfield Residential", "rep": "Salam", "status": "prospect",
        "website": "http://www.fairfieldresidential.com", "phone": "(858) 457-2123",
        "addresses": ["5355 Mira Sorrento Place, Suite 100, San Diego, CA 92121"],
        "emails": [], "numProperties": 13, "totalUnits": 3242, "notes": "", "contacts": [],
    },
    {
        "name": "Hoban Management, Inc.", "rep": "RJ", "status": "contacted",
        "website": "http://www.hobanmanagement.com", "phone": "(619) 442-1665",
        "addresses": ["215 W Lexington Ave, El Cajon, CA 92020"],
        "emails": [], "numProperties": 31, "totalUnits": 2260, "notes": "", "contacts": [],
    },
    {
        "name": "Whittington Property Management", "rep": "", "status": "prospect",
        "website": "http://www.whittingtonpm.com", "phone": "(619) 667-3050",
        "addresses": ["8270 La Mesa Blvd, Suite 202, La Mesa, CA 91942"],
        "emails": [], "numProperties": 9, "totalUnits": 866,
        "notes": "Unassigned — up for grabs.", "contacts": [],
    },
]


def seed_accounts(db: Session) -> None:
    """Seed the pilot accounts on an empty accounts table (idempotent)."""
    if db.query(Account).count() > 0:
        return
    for item in SEED_ACCOUNTS:
        a = Account(
            name=item["name"], rep=item.get("rep", ""), status=item.get("status", "prospect"),
            website=item.get("website", ""), phone=item.get("phone", ""),
            addresses_json=json.dumps(item.get("addresses", [])),
            emails_json=json.dumps(item.get("emails", [])),
            num_properties=item.get("numProperties"), total_units=item.get("totalUnits"),
            notes=item.get("notes", ""), updated_by="seed",
        )
        for c in item.get("contacts", []):
            a.contacts.append(AccountContact(
                name=c["name"], role=c.get("role", ""), email=c.get("email", ""),
                phone=c.get("phone", ""), address=c.get("address", ""),
            ))
        db.add(a)
    db.commit()
