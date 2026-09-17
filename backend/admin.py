"""Admin API: system prompt sections + glossary management.

Only users with `is_admin=True` can access these endpoints. The company-wide
system prompt is assembled from SystemPromptSection rows (sorted by
sort_order) at chat time; glossary entries from the existing GlossaryEntry
table are appended as a final section.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import m365
from database import get_db
from models import GlossaryEntry, SystemPromptSection, User
from schemas import GlossaryIn, PromptSectionIn

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(
    user: User = Depends(m365.get_session_user),
) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin access required")
    return user


# --- System prompt sections --------------------------------------------------

@router.get("/prompt")
def list_prompt_sections(
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(SystemPromptSection)
        .order_by(SystemPromptSection.sort_order)
        .all()
    )
    return [
        {
            "id": r.id,
            "key": r.key,
            "label": r.label,
            "content": r.content,
            "sort_order": r.sort_order,
            "active": r.active,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "updated_by": r.updated_by or "",
        }
        for r in rows
    ]


@router.put("/prompt/{key}")
def update_prompt_section(
    key: str,
    payload: PromptSectionIn,
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict:
    section = db.query(SystemPromptSection).filter(
        SystemPromptSection.key == key
    ).first()
    if section is None:
        raise HTTPException(404, f"Prompt section '{key}' not found")
    if payload.content is not None:
        section.content = payload.content
    if payload.label is not None:
        section.label = payload.label
    if payload.sort_order is not None:
        section.sort_order = payload.sort_order
    if payload.active is not None:
        section.active = payload.active
    section.updated_at = datetime.now(timezone.utc)
    section.updated_by = user.name or user.email or ""
    db.commit()
    log.info("Admin %s updated prompt section '%s'", user.email, key)
    return {
        "id": section.id,
        "key": section.key,
        "label": section.label,
        "content": section.content,
        "sort_order": section.sort_order,
        "active": section.active,
        "updated_at": section.updated_at.isoformat() if section.updated_at else None,
        "updated_by": section.updated_by,
    }


# --- Glossary (company-wide, shared with email triage) -----------------------

@router.get("/glossary")
def list_glossary(
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.query(GlossaryEntry).order_by(GlossaryEntry.term).all()
    return [{"id": r.id, "term": r.term, "meaning": r.meaning} for r in rows]


@router.post("/glossary", status_code=201)
def add_glossary_entry(
    payload: GlossaryIn,
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict:
    entry = GlossaryEntry(term=payload.term.strip(), meaning=payload.meaning.strip())
    db.add(entry)
    db.commit()
    log.info("Admin %s added glossary entry '%s'", user.email, entry.term)
    return {"id": entry.id, "term": entry.term, "meaning": entry.meaning}


@router.put("/glossary/{entry_id}")
def update_glossary_entry(
    entry_id: int,
    payload: GlossaryIn,
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict:
    entry = db.get(GlossaryEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Glossary entry not found")
    entry.term = payload.term.strip()
    entry.meaning = payload.meaning.strip()
    db.commit()
    log.info("Admin %s updated glossary entry %d", user.email, entry_id)
    return {"id": entry.id, "term": entry.term, "meaning": entry.meaning}


@router.delete("/glossary/{entry_id}", status_code=204)
def delete_glossary_entry(
    entry_id: int,
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> None:
    entry = db.get(GlossaryEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Glossary entry not found")
    db.delete(entry)
    db.commit()
    log.info("Admin %s deleted glossary entry %d ('%s')", user.email, entry_id, entry.term)
