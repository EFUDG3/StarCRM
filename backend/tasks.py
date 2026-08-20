"""Tasks tab: a read-only "today" aggregator over the signed-in user's OWN
M365 + CRM data. Assembles three lanes and leaves the manual to-do rail to the
existing /api/todos:

- Meetings   — the next ~7 days of calendar events (delegated Calendars.Read).
- Replies    — emails the user flagged in Outlook (Mail.Read), minus the ones
               they've dismissed here. NOTHING writes to M365: the row links out
               to Outlook, and a flag cleared there drops off the next refresh.
- People     — CRM next-actions on the signed-in user's own contacts.

All reads use existing scopes — no new Graph permissions, no admin consent.
"""
from datetime import date as date_cls, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import graph
import m365
import telemetry
from database import get_db
from models import Contact, DismissedEmail, Interaction, User
from schemas import DismissIn

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# San Diego company — "today" is Pacific, matching the calendar times Graph
# returns (graph.list_calendar_events renders in America/Los_Angeles).
_PT = ZoneInfo("America/Los_Angeles")


def _today_pt() -> date_cls:
    return datetime.now(_PT).date()


@router.get("/agenda")
def agenda(
    user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> dict:
    """The assembled day: meetings + flagged-email replies + CRM follow-ups.
    Per-lane Graph failures degrade to an empty lane rather than sinking the
    whole response (so the CRM lane still works if the mailbox call hiccups)."""
    from datetime import timedelta

    graph_token = m365.get_graph_token(user, db)
    today = _today_pt()
    window_end = today + timedelta(days=6)

    meetings = []
    try:
        meetings = graph.list_calendar_events(
            graph_token, today.isoformat(), window_end.isoformat(), top=60
        )
    except graph.GraphError:
        meetings = []

    replies = []
    try:
        dismissed = {
            d.message_id
            for d in db.query(DismissedEmail).filter(DismissedEmail.user_id == user.id).all()
        }
        replies = [
            m for m in graph.list_flagged_messages(graph_token, top=40)
            if m.get("id") not in dismissed
        ]
    except graph.GraphError:
        replies = []

    # People: CRM follow-ups on THIS user's own contacts (session-scoped, not the
    # X-User-Id profile switcher). `!= ""` also excludes NULLs in SQL.
    rows = (
        db.query(Contact)
        .filter(Contact.user_id == user.id, Contact.next_action != "")
        .all()
    )
    followups = sorted(
        [
            {
                "id": c.id,
                "name": c.name,
                "company": c.company or "",
                "category": c.category or "bd",
                "nextAction": c.next_action or "",
                "nextDue": c.next_due or "",
            }
            for c in rows
        ],
        key=lambda f: (f["nextDue"] or "9999"),
    )

    telemetry.log_event(user.id, "tasks", "agenda")
    return {
        "today": today.isoformat(),
        "meetings": meetings,
        "replies": replies,
        "followups": followups,
    }


@router.post("/dismiss", status_code=204)
def dismiss_reply(
    payload: DismissIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    """Hide a flagged email from this user's Replies lane, permanently (survives
    refreshes even if the Outlook flag stays set)."""
    mid = (payload.id or "").strip()
    if not mid:
        raise HTTPException(status_code=400, detail="id is required")
    exists = (
        db.query(DismissedEmail)
        .filter(DismissedEmail.user_id == user.id, DismissedEmail.message_id == mid)
        .first()
    )
    if not exists:
        db.add(DismissedEmail(user_id=user.id, message_id=mid))
        db.commit()
        telemetry.log_event(user.id, "tasks", "reply_dismissed")


@router.post("/followup/{contact_id}/complete")
def complete_followup(
    contact_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Complete a CRM follow-up from the Tasks People lane — logs it as a done
    touch on the contact and clears the next action. Session-user scoped so it
    always hits the signed-in user's own contact (not the X-User-Id profile)."""
    c = (
        db.query(Contact)
        .filter(Contact.id == contact_id, Contact.user_id == user.id)
        .first()
    )
    if c is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    done_note = "Done: " + (c.next_action or "follow-up")
    c.interactions.append(Interaction(date=date_cls.today().isoformat(), note=done_note))
    c.next_action = ""
    c.next_due = ""
    db.commit()
    telemetry.log_event(user.id, "crm", "action_completed")
    return {"ok": True, "contactId": contact_id}
