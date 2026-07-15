"""Usage telemetry: one row per meaningful user action.

Rules:
- log_event is fire-and-forget on its own short-lived session; any failure is
  swallowed. Telemetry must never break or slow the feature it observes.
- Only mutations and per-use costs are logged (chat turns, tool calls, route
  calculations, saves). List/read endpoints fire on every render and would be
  noise, so they are not logged.
- compute_stats returns aggregate counts only — never message or contact
  content. The /api/stats route in main.py gates it behind the company sign-in.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Event

_log = logging.getLogger("uvicorn.error")


def log_event(user_id: str | None, area: str, action: str, detail: str = "") -> None:
    """Record one usage event. Best-effort: opens (and closes) its own session
    so a telemetry failure can never poison the caller's transaction."""
    try:
        db = SessionLocal()
        try:
            db.add(Event(user_id=user_id, area=area, action=action, detail=(detail or "")[:500]))
            db.commit()
        finally:
            db.close()
    except Exception:
        _log.debug("telemetry write failed", exc_info=True)


def compute_stats(db: Session, days: int = 30) -> dict:
    """Aggregate usage for the last N days: totals by area, top actions,
    active users, and a per-day series."""
    days = max(1, min(days, 365))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    window = db.query(Event).filter(Event.created_at >= cutoff)

    by_area = dict(
        window.with_entities(Event.area, sa_func.count())
        .group_by(Event.area).all()
    )
    top_actions = [
        {"area": a, "action": ac, "count": n}
        for a, ac, n in window.with_entities(Event.area, Event.action, sa_func.count())
        .group_by(Event.area, Event.action)
        .order_by(sa_func.count().desc()).limit(20).all()
    ]
    per_day = [
        {"day": d.date().isoformat(), "count": n}
        for d, n in window.with_entities(
            sa_func.date_trunc("day", Event.created_at), sa_func.count())
        .group_by(sa_func.date_trunc("day", Event.created_at))
        .order_by(sa_func.date_trunc("day", Event.created_at)).all()
    ]
    active_users = window.with_entities(
        sa_func.count(sa_func.distinct(Event.user_id))).scalar() or 0

    return {
        "days": days,
        "total": sum(by_area.values()),
        "activeUsers": active_users,
        "byArea": by_area,
        "topActions": top_actions,
        "perDay": per_day,
    }
