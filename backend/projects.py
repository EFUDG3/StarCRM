"""Shared Projects board — the PM team's large commercial construction jobs.

Company-wide like Accounts (any signed-in Star user sees and edits every
project), NOT per-profile like Contacts. `pm` marks who runs the job; blank =
unassigned. `updated_at`/`updated_by` make a stale job obvious at a glance.

Scope is deliberately lean: this exists so project managers walk into a weekly
meeting knowing where every job stands. Stage drives a grouped board with
"In progress" as the main lane; the dated activity log IS the status history.
Money beyond a reference contract value belongs in QuickBooks, and document
storage (flooring plan PDFs) is intentionally NOT here.

Distinct from accounts.py: that's the SALES hit list (property management
companies). These are PM jobs, which arrive via GCs and owners — no link
between the two tables by design.
"""
from datetime import date as date_cls, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

import m365
import telemetry
from database import get_db
from models import Project, User
from models import ProjectInteraction
from schemas import ProjectIn, ProjectLogIn, _STAGES as VALID_STAGES

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today() -> str:
    return date_cls.today().isoformat()


def serialize(p: Project) -> dict:
    """Shape one project for the frontend (snake_case -> camelCase, newest-first
    activity log)."""
    log = sorted(
        p.interactions,
        key=lambda i: (i.date, i.created_at or datetime.min),
        reverse=True,
    )
    return {
        "id": p.id,
        "name": p.name,
        "client": p.client or "",
        "siteAddress": p.site_address or "",
        "projectType": p.project_type or "other",
        "stage": p.stage or "in_progress",
        "pm": p.pm or "",
        "contractValue": p.contract_value,
        "startDate": p.start_date or "",
        "targetDate": p.target_date or "",
        "material": p.material or "",
        "sqFt": p.sq_ft,
        "description": p.description or "",
        "created": p.created_at.date().isoformat() if p.created_at else "",
        "updated": p.updated_at.isoformat() if p.updated_at else "",
        "updatedBy": p.updated_by or "",
        "log": [{"date": i.date, "note": i.note, "by": i.by or ""} for i in log],
    }


def _apply(p: Project, payload: ProjectIn, user: User) -> None:
    """Copy a validated payload onto a project and stamp the editor."""
    p.name = payload.name.strip()
    p.client = (payload.client or "").strip()
    p.site_address = (payload.siteAddress or "").strip()
    p.project_type = (payload.projectType or "other").strip() or "other"
    p.stage = (payload.stage or "in_progress").strip() or "in_progress"
    p.pm = (payload.pm or "").strip()
    p.contract_value = payload.contractValue
    p.start_date = (payload.startDate or "").strip()
    p.target_date = (payload.targetDate or "").strip()
    p.material = (payload.material or "").strip()
    p.sq_ft = payload.sqFt
    p.description = payload.description or ""
    p.updated_at = _now()
    p.updated_by = user.name


@router.get("")
def list_projects(
    _user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> list:
    """Every project, for everyone. The frontend groups by stage.

    Eager-loads the job log for the same reason accounts does: serialize()
    reads it on every row, so lazy loading would be one extra query per
    project."""
    rows = db.query(Project).options(selectinload(Project.interactions)).all()
    return [serialize(p) for p in rows]


@router.post("", status_code=201)
def create_project(
    payload: ProjectIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    p = Project()
    _apply(p, payload, user)
    db.add(p)
    db.commit()
    db.refresh(p)
    telemetry.log_event(user.id, "projects", "project_created")
    return serialize(p)


@router.get("/{project_id}")
def get_project(
    project_id: str,
    _user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return serialize(p)


@router.put("/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    _apply(p, payload, user)
    db.commit()
    db.refresh(p)
    telemetry.log_event(user.id, "projects", "project_updated")
    return serialize(p)


@router.post("/{project_id}/log")
def log_note(
    project_id: str,
    payload: ProjectLogIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Append a dated note to the project's job log and stamp who logged it."""
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    note = payload.note.strip()
    if not note:
        raise HTTPException(status_code=400, detail="Note is required")
    p.interactions.append(
        ProjectInteraction(date=(payload.date or _today()), note=note, by=user.name)
    )
    p.updated_at = _now()
    p.updated_by = user.name
    db.commit()
    db.refresh(p)
    telemetry.log_event(user.id, "projects", "note_logged")
    return serialize(p)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(p)  # project_interactions cascade
    db.commit()
    telemetry.log_event(user.id, "projects", "project_deleted")


# --- Operations for starbot chat (and, later, the Claude connector) ----------
#
# Same shape and reasoning as the account ops: shared data (no user scoping),
# compact rows on list so the whole job board doesn't flood the model's
# context, and a narrow write surface — log a note and move stage. NO create
# and NO delete from chat, since this table is team-shared.

_PROJ_LIST_DEFAULT = 25
_PROJ_LIST_MAX = 60

# Live stages get date-ordered; closed ones fall to the bottom. Mirrors the
# LIVE_STAGES set the frontend board uses for its urgency badge.
_LIVE = ("bidding", "awarded", "in_progress", "punch_list")


def _compact_project(p: Project) -> dict:
    """One project as a summary row (job log reduced to its latest entry)."""
    log = sorted(
        p.interactions, key=lambda i: (i.date, i.created_at or datetime.min), reverse=True
    )
    latest = log[0] if log else None
    return {
        "id": p.id,
        "name": p.name,
        "client": p.client or "",
        "stage": p.stage or "in_progress",
        "pm": p.pm or "",
        "projectType": p.project_type or "other",
        "siteAddress": p.site_address or "",
        "startDate": p.start_date or "",
        "targetDate": p.target_date or "",
        "contractValue": p.contract_value,
        "sqFt": p.sq_ft,
        "material": p.material or "",
        "logEntries": len(log),
        "latestLog": (
            {"date": latest.date, "note": latest.note[:200], "by": latest.by or ""}
            if latest else None
        ),
    }


def _all_projects(db: Session) -> list[Project]:
    # _compact_project reads interactions on every row — eager-load them.
    return db.query(Project).options(selectinload(Project.interactions)).all()


def op_project_list(
    db: Session, stage: str = "", pm: str = "", query: str = "", limit: int = 0
) -> dict:
    """The shared job board. Live jobs first, ordered by soonest target date
    (undated last); closed jobs after, most recently touched first."""
    everything = _all_projects(db)
    total = len(everything)
    # Stage tally always reflects the WHOLE board, not the filtered slice, so
    # the model can say "3 in progress, 2 bidding" even on a narrow query.
    counts: dict[str, int] = {}
    for p in everything:
        k = p.stage or "in_progress"
        counts[k] = counts.get(k, 0) + 1

    rows = list(everything)
    st = (stage or "").strip().lower()
    if st:
        if st not in VALID_STAGES:
            return {"error": f"Unknown stage '{stage}'. Valid: {sorted(VALID_STAGES)}"}
        rows = [p for p in rows if (p.stage or "in_progress") == st]
    m = (pm or "").strip().lower()
    if m:
        rows = [
            p for p in rows
            if any(t.strip().startswith(m) for t in (p.pm or "").lower().split("/"))
        ]
    q = (query or "").strip().lower()
    if q:
        rows = [
            p for p in rows
            if q in " ".join([
                p.name, p.client or "", p.site_address or "", p.material or "",
                p.description or "", p.project_type or "", p.pm or "",
            ]).lower()
        ]

    matched = len(rows)

    def sort_key(p: Project):
        live = (p.stage or "in_progress") in _LIVE
        if live:
            # Undated live jobs sort after dated ones, not before.
            return (0, p.target_date or "9999-12-31", "")
        return (1, "", p.updated_at.isoformat() if p.updated_at else "")

    rows.sort(key=sort_key)
    cap = max(1, min(int(limit or _PROJ_LIST_DEFAULT), _PROJ_LIST_MAX))
    out = rows[:cap]

    return {
        "totalProjects": total,
        "byStage": counts,
        "matched": matched,
        "returned": len(out),
        "truncated": matched > len(out),
        "projects": [_compact_project(p) for p in out],
    }


def op_project_get(db: Session, project_id: str) -> dict:
    """One project in full, including the entire job log."""
    p = db.get(Project, project_id)
    if p is None:
        return {"error": f"No project with id {project_id}"}
    return serialize(p)


def op_project_log(
    db: Session, user: User, project_id: str, note: str, date: str | None = None
) -> dict:
    """Append a dated entry to a project's job log, stamped with who logged it
    (mirrors POST /api/projects/{id}/log)."""
    p = db.get(Project, project_id)
    if p is None:
        return {"error": f"No project with id {project_id}"}
    text = (note or "").strip()
    if not text:
        return {"error": "note is required"}
    p.interactions.append(
        ProjectInteraction(date=(date or _today()), note=text[:2000], by=user.name)
    )
    p.updated_at = _now()
    p.updated_by = user.name
    db.commit()
    db.refresh(p)
    telemetry.log_event(user.id, "projects", "note_logged", "chat")
    return serialize(p)


def op_project_set_stage(db: Session, user: User, project_id: str, stage: str) -> dict:
    """Move a job to another stage. Rejects unknown values rather than coercing,
    so a wrong guess comes back as an error the model can correct (the HTTP
    layer coerces instead, because a form should never hard-fail)."""
    p = db.get(Project, project_id)
    if p is None:
        return {"error": f"No project with id {project_id}"}
    st = (stage or "").strip().lower()
    if st not in VALID_STAGES:
        return {"error": f"Unknown stage '{stage}'. Valid: {sorted(VALID_STAGES)}"}
    p.stage = st
    p.updated_at = _now()
    p.updated_by = user.name
    db.commit()
    db.refresh(p)
    telemetry.log_event(user.id, "projects", "project_updated", "chat:stage")
    return _compact_project(p)
