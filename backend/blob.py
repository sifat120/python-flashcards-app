"""Blob storage abstraction — local filesystem by default, Azure Blob on Embr.

Default: uploads are written to `backend/uploads/` and served by
the app at `/uploads/{key}`. This lets the app run out-of-the-box with
zero configuration.

Production (Embr): when `EMBR_BLOB_KEY` is set, Embr has provisioned a
per-environment blob store. Uploads go to that, and files are served from the
environment's public blob URL (no egress through the app). See:
https://docs.embr.dev (blob-storage-design)

To enable Azure Blob directly (outside Embr):
  1. `pip install azure-storage-blob` (uncomment in requirements.txt)
  2. Set AZURE_STORAGE_CONNECTION_STRING and AZURE_BLOB_CONTAINER env vars.
  3. Replace the `_LocalStore` body with BlobServiceClient calls.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "svg", "gif"}
MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class UploadError(Exception):
    pass


class _LocalStore:
    """Saves files to backend/uploads/, serves them at /uploads/{key}."""

    def save(self, filename: str, data: bytes) -> tuple[str, str]:
        ext = (filename.rsplit(".", 1)[-1] or "").lower()
        if ext not in ALLOWED_EXTS:
            raise UploadError(f"unsupported file type: .{ext}")
        if len(data) > MAX_SIZE_BYTES:
            raise UploadError(f"file exceeds {MAX_SIZE_BYTES // 1024 // 1024} MB limit")
        key = f"{uuid.uuid4().hex}.{ext}"
        (UPLOADS_DIR / key).write_bytes(data)
        return key, f"/uploads/{key}"

    def path_for(self, key: str) -> Path:
        return UPLOADS_DIR / key


# Uncomment to use Azure Blob directly:
#
# from azure.storage.blob import BlobServiceClient, ContentSettings
#
# class _AzureBlobStore:
#     def __init__(self):
#         conn = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
#         self._container = os.environ.get("AZURE_BLOB_CONTAINER", "flashcard-images")
#         self._svc = BlobServiceClient.from_connection_string(conn)
#         self._svc.get_container_client(self._container).create_container(exists_ok=True)
#
#     def save(self, filename, data):
#         ext = filename.rsplit(".", 1)[-1].lower()
#         if ext not in ALLOWED_EXTS: raise UploadError(...)
#         if len(data) > MAX_SIZE_BYTES: raise UploadError(...)
#         key = f"{uuid.uuid4().hex}.{ext}"
#         client = self._svc.get_blob_client(self._container, key)
#         client.upload_blob(data, content_settings=ContentSettings(content_type=f"image/{ext}"))
#         return key, client.url
#
# On Embr: EMBR_BLOB_KEY is injected automatically. Upload via:
#   PUT https://<env-domain>/_embr/blob/{key}
#   Authorization: Bearer ${EMBR_BLOB_KEY}
# Reads are public at the same URL — no auth required.


def is_embr_blob_enabled() -> bool:
    return bool(os.getenv("EMBR_BLOB_KEY"))


store = _LocalStore()
