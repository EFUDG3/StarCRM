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
from schemas import ProjectIn, ProjectLogIn

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
