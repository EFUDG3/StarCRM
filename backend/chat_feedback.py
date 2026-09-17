"""Chat feedback + per-user preferences API.

Two concerns, one router:

  1. FEEDBACK  Thumbs up/down on assistant responses. Each row is a signal for
              the Phase 4 pattern detector; for now it is stored and queryable.

  2. PREFERENCES  Per-user behavior preferences injected into the system prompt.
                  The user writes free-text guidance lines ("I prefer bullet
                  points", "Always check my calendar first") that shape how the
                  model responds. CRUD is here; injection is in chat._system_prompt().
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import m365
import telemetry
from database import get_db
from models import ChatFeedback, ChatPreference, GlossaryEntry, SystemPromptSection, User
from schemas import ChatFeedbackIn, ChatPreferenceIn

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/chat", tags=["chat-feedback"])


def _serialize_feedback(f: ChatFeedback) -> dict:
    return {
        "id": f.id,
        "rating": f.rating,
        "chip": f.chip,
        "correction": f.correction or "",
        "user_message_preview": f.user_message_preview or "",
        "assistant_message_preview": f.assistant_message_preview or "",
        "tools_used": json.loads(f.tools_used_json or "[]"),
        "status": f.status,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _serialize_pref(p: ChatPreference) -> dict:
    return {
        "id": p.id,
        "text": p.text,
        "category": p.category,
        "source": p.source,
        "active": p.active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


# --- System prompt (read-only for any signed-in user) ------------------------

@router.get("/system-prompt")
def get_system_prompt(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return prompt sections + glossary for the rules UI. Any signed-in user
    can read this (it shapes every conversation they have), but only admins
    can edit via /api/admin/prompt."""
    sections = (
        db.query(SystemPromptSection)
        .order_by(SystemPromptSection.sort_order)
        .all()
    )
    glossary = db.query(GlossaryEntry).order_by(GlossaryEntry.term).all()
    return {
        "sections": [
            {
                "key": s.key,
                "label": s.label,
                "content": s.content,
                "active": s.active,
            }
            for s in sections
        ],
        "glossary": [
            {"term": g.term, "meaning": g.meaning}
            for g in glossary
        ],
        "isAdmin": user.is_admin,
    }


# --- Feedback ----------------------------------------------------------------

@router.post("/feedback", status_code=201)
def submit_feedback(
    payload: ChatFeedbackIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    fb = ChatFeedback(
        user_id=user.id,
        rating=payload.rating,
        chip=payload.chip if payload.rating == "down" else None,
        correction=payload.correction.strip()[:1000] if payload.correction else "",
        user_message_preview=(payload.user_message_preview or "")[:300],
        assistant_message_preview=(payload.assistant_message_preview or "")[:300],
        tools_used_json=json.dumps(payload.tools_used[:20] if payload.tools_used else []),
    )
    db.add(fb)
    db.commit()
    telemetry.log_event(user.id, "chat", f"feedback:{payload.rating}",
                        payload.chip or "")
    log.info("Chat feedback %s from %s: %s%s", fb.id, user.email, payload.rating,
             f" ({payload.chip})" if payload.chip else "")
    return _serialize_feedback(fb)


@router.get("/feedback")
def list_feedback(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(ChatFeedback)
        .filter(ChatFeedback.user_id == user.id)
        .order_by(ChatFeedback.created_at.desc())
        .limit(100)
        .all()
    )
    return [_serialize_feedback(f) for f in rows]


# --- Preferences -------------------------------------------------------------

@router.get("/preferences")
def list_preferences(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(ChatPreference)
        .filter(ChatPreference.user_id == user.id)
        .order_by(ChatPreference.created_at)
        .all()
    )
    return [_serialize_pref(p) for p in rows]


@router.post("/preferences", status_code=201)
def create_preference(
    payload: ChatPreferenceIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    pref = ChatPreference(
        user_id=user.id,
        text=payload.text.strip(),
        category=payload.category,
        source="manual",
    )
    db.add(pref)
    db.commit()
    telemetry.log_event(user.id, "chat", "preference:create", payload.category)
    log.info("Chat preference %s created by %s: %s", pref.id, user.email,
             payload.text[:60])
    return _serialize_pref(pref)


@router.put("/preferences/{pref_id}")
def update_preference(
    pref_id: str,
    payload: ChatPreferenceIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    pref = (
        db.query(ChatPreference)
        .filter(ChatPreference.id == pref_id, ChatPreference.user_id == user.id)
        .first()
    )
    if pref is None:
        raise HTTPException(404, "Preference not found")
    pref.text = payload.text.strip()
    pref.category = payload.category
    if payload.active is not None:
        pref.active = payload.active
    db.commit()
    log.info("Chat preference %s updated by %s", pref_id, user.email)
    return _serialize_pref(pref)


@router.delete("/preferences/{pref_id}", status_code=204)
def delete_preference(
    pref_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    pref = (
        db.query(ChatPreference)
        .filter(ChatPreference.id == pref_id, ChatPreference.user_id == user.id)
        .first()
    )
    if pref is None:
        raise HTTPException(404, "Preference not found")
    db.delete(pref)
    db.commit()
    telemetry.log_event(user.id, "chat", "preference:delete", "")
    log.info("Chat preference %s deleted by %s", pref_id, user.email)
