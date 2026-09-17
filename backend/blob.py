"""Azure Blob Storage helpers for user file uploads.

Design: files never stream through the container app. Upload goes
client -> backend (multipart) -> Blob (SDK put_blob). Download uses
a short-lived SAS read URL so the browser fetches directly from Azure.

Env: AZURE_STORAGE_CONNECTION_STRING (the full connection string from
the Azure portal). Without it the module is inert: upload/download
raise a clear error, and `is_configured()` returns False so the UI
can grey out the upload button.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from azure.storage.blob import (
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
    BlobSasPermissions,
)

log = logging.getLogger("uvicorn.error")

_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
CONTAINER = os.getenv("BLOB_CONTAINER_FILES", "user-files")

_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per upload


def is_configured() -> bool:
    return bool(_CONN_STR)


def _client() -> BlobServiceClient:
    if not _CONN_STR:
        raise RuntimeError(
            "AZURE_STORAGE_CONNECTION_STRING is not set. "
            "File uploads require an Azure Blob Storage account."
        )
    return BlobServiceClient.from_connection_string(_CONN_STR)


def _ensure_container(svc: BlobServiceClient) -> None:
    try:
        svc.create_container(CONTAINER)
    except Exception:
        pass  # already exists


def upload_file(user_id: str, filename: str, data: bytes, content_type: str) -> str:
    """Upload bytes to Blob Storage. Returns the blob_key."""
    svc = _client()
    _ensure_container(svc)
    blob_key = f"{user_id}/{uuid.uuid4().hex[:8]}_{filename}"
    blob = svc.get_blob_client(container=CONTAINER, blob=blob_key)
    blob.upload_blob(data, overwrite=True, content_settings=ContentSettings(
        content_type=content_type,
    ))
    log.info("Blob uploaded: %s (%d bytes)", blob_key, len(data))
    return blob_key


def download_file(blob_key: str) -> bytes:
    """Download a blob's full content as bytes."""
    svc = _client()
    client = svc.get_blob_client(container=CONTAINER, blob=blob_key)
    return client.download_blob().readall()


def get_download_url(blob_key: str, expiry_minutes: int = 10) -> str:
    """Generate a short-lived SAS read URL for a blob."""
    svc = _client()
    account_name = svc.account_name
    account_key = svc.credential.account_key
    sas = generate_blob_sas(
        account_name=account_name,
        container_name=CONTAINER,
        blob_name=blob_key,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
    )
    return f"https://{account_name}.blob.core.windows.net/{CONTAINER}/{blob_key}?{sas}"


def delete_blob(blob_key: str) -> None:
    """Delete a blob. Best-effort; logs but does not raise on failure."""
    try:
        svc = _client()
        blob = svc.get_blob_client(container=CONTAINER, blob=blob_key)
        blob.delete_blob()
        log.info("Blob deleted: %s", blob_key)
    except Exception:
        log.warning("Failed to delete blob %s", blob_key, exc_info=True)
