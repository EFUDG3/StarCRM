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
from sqlalchemy.orm import Session

import m365
import telemetry
from database import get_db
from models import Account, AccountContact, AccountInteraction, User
from schemas import AccountIn, AccountLogIn

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
    """Every account, for everyone. Frontend does the ranking/filtering."""
    rows = db.query(Account).all()
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
