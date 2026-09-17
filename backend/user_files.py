"""User file uploads — context files for AI conversations.

Users upload documents (PDFs, images, spreadsheets, text files) that become
part of their chat context. File content lives in Azure Blob Storage; this
module manages the metadata rows and orchestrates upload/download/delete.

Privacy model (the "metadata shadow"):
  - Everyone's system prompt sees "Amanda uploaded floor-plans.pdf on Sep 15"
  - Only Amanda's chat can fetch the actual content via the download endpoint
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

import blob
import telemetry
from database import get_db
import m365
from models import User, UserFile

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/chat", tags=["user-files"])

_ALLOWED_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel",
    "application/msword",
    "image/jpeg",
    "image/png",
    "image/webp",
}

_MAX_FILE_SIZE = blob._MAX_FILE_SIZE  # 50 MB


def _serialize(f: UserFile) -> dict:
    return {
        "id": f.id,
        "filename": f.filename,
        "contentType": f.content_type,
        "sizeBytes": f.size_bytes,
        "summary": f.summary,
        "uploadedAt": f.uploaded_at.isoformat() if f.uploaded_at else None,
    }


@router.get("/files")
def list_files(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(UserFile)
        .filter(UserFile.user_id == user.id)
        .order_by(UserFile.uploaded_at.desc())
        .all()
    )
    return [_serialize(f) for f in rows]


@router.get("/files/status")
def files_status(
    user: User = Depends(m365.get_session_user),
) -> dict:
    """Check whether file uploads are available (Blob Storage configured)."""
    return {"configured": blob.is_configured()}


@router.post("/files", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    if not blob.is_configured():
        raise HTTPException(503, "File uploads are not configured yet. Azure Blob Storage connection required.")

    ct = file.content_type or "application/octet-stream"
    if ct not in _ALLOWED_TYPES:
        raise HTTPException(
            400,
            f"File type '{ct}' is not supported. "
            f"Allowed: PDF, Word, Excel, PowerPoint, images, text, CSV."
        )

    data = await file.read()
    if len(data) > _MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Maximum size is {_MAX_FILE_SIZE // (1024*1024)} MB.")
    if len(data) == 0:
        raise HTTPException(400, "File is empty.")

    filename = file.filename or "unnamed"
    blob_key = blob.upload_file(user.id, filename, data, ct)

    summary = _generate_summary(filename, ct, len(data))

    uf = UserFile(
        user_id=user.id,
        filename=filename,
        content_type=ct,
        size_bytes=len(data),
        blob_key=blob_key,
        summary=summary,
    )
    db.add(uf)
    db.commit()

    telemetry.log_event(user.id, "chat", "file:upload", filename[:60])
    log.info("File %s uploaded by %s: %s (%d bytes)", uf.id, user.email, filename, len(data))
    return _serialize(uf)


@router.get("/files/{file_id}/download")
def download_file(
    file_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return a short-lived SAS download URL. Only the file owner can download."""
    uf = (
        db.query(UserFile)
        .filter(UserFile.id == file_id, UserFile.user_id == user.id)
        .first()
    )
    if uf is None:
        raise HTTPException(404, "File not found")

    url = blob.get_download_url(uf.blob_key)
    return {"url": url, "filename": uf.filename}


@router.delete("/files/{file_id}", status_code=204)
def delete_file(
    file_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    uf = (
        db.query(UserFile)
        .filter(UserFile.id == file_id, UserFile.user_id == user.id)
        .first()
    )
    if uf is None:
        raise HTTPException(404, "File not found")

    blob.delete_blob(uf.blob_key)
    db.delete(uf)
    db.commit()

    telemetry.log_event(user.id, "chat", "file:delete", uf.filename[:60])
    log.info("File %s deleted by %s: %s", file_id, user.email, uf.filename)


def _generate_summary(filename: str, content_type: str, size_bytes: int) -> str:
    """Generate a simple description of the file for the system prompt.

    Phase 3 v1: a mechanical summary from the filename and type.
    Future: pass the file content through Claude to generate a real summary
    (extract text from PDFs, describe images, etc.)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    type_label = {
        "pdf": "PDF document",
        "docx": "Word document",
        "doc": "Word document",
        "xlsx": "Excel spreadsheet",
        "xls": "Excel spreadsheet",
        "pptx": "PowerPoint presentation",
        "csv": "CSV data file",
        "txt": "text file",
        "md": "Markdown document",
        "jpg": "JPEG image",
        "jpeg": "JPEG image",
        "png": "PNG image",
        "webp": "WebP image",
    }.get(ext, "file")
    size_label = _fmt_size(size_bytes)
    return f"{type_label} ({size_label})"


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
