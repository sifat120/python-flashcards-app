"""Blob storage abstraction.

Two interchangeable backends, picked at import time:
  - Embr backend  → used when `EMBR_BLOB_KEY` is set (managed blob on Embr).
                    Uploads go to `https://<env-domain>/_embr/blob/{key}` and
                    are served publicly from that same URL.
  - Local backend → used when no `EMBR_BLOB_KEY` is present (local dev).
                    Uploads land in `backend/uploads/` and are served by the
                    app at `/uploads/{key}`.

Both expose a `store` singleton with a `save(filename, data) -> (key, url)`
method so the API layer in `backend/app.py` is backend-agnostic.

Used by the flashcard app to store image attachments on cards (e.g. the seed
diagrams or any custom image a user uploads through the UI).

On Embr both env vars below are injected automatically — no configuration
required:
  - EMBR_BLOB_KEY  : Bearer token for write/list/delete (reads are public).
  - EMBR_DOMAIN    : the environment's public hostname.
"""

from __future__ import annotations

import logging
import os
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "svg", "gif"}
MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "gif": "image/gif",
}


class UploadError(Exception):
    pass


def _validate(filename: str, data: bytes) -> str:
    ext = (filename.rsplit(".", 1)[-1] or "").lower()
    if ext not in ALLOWED_EXTS:
        raise UploadError(f"unsupported file type: .{ext}")
    if len(data) > MAX_SIZE_BYTES:
        raise UploadError(f"file exceeds {MAX_SIZE_BYTES // 1024 // 1024} MB limit")
    return ext


# ── Local backend (default for local dev) ────────────────────────────────────


class _LocalStore:
    """Saves files to backend/uploads/, serves them at /uploads/{key}."""

    def save(self, filename: str, data: bytes) -> tuple[str, str]:
        ext = _validate(filename, data)
        key = f"{uuid.uuid4().hex}.{ext}"
        (UPLOADS_DIR / key).write_bytes(data)
        return key, f"/uploads/{key}"

    def path_for(self, key: str) -> Path:
        return UPLOADS_DIR / key


# ── Embr backend (managed blob) ──────────────────────────────────────────────


class _EmbrBlobStore:
    """Uploads to Embr's per-environment blob store via PUT /_embr/blob/{key}.

    Reads are public at the same URL — served by the Embr platform proxy
    (never hits this app), so the returned URL is a same-domain relative path
    that the browser can use directly in <img src="...">.
    """

    def __init__(self, blob_key: str, domain: Optional[str]):
        self._blob_key = blob_key
        # EMBR_DOMAIN is injected per-environment; fall back to localhost so
        # local testing of the Embr code-path is at least possible.
        self._base = f"https://{domain}" if domain else "http://localhost:8080"

    def save(self, filename: str, data: bytes) -> tuple[str, str]:
        ext = _validate(filename, data)
        key = f"images/{uuid.uuid4().hex}.{ext}"
        url_path = f"/_embr/blob/{key}"
        upload_url = f"{self._base}{url_path}"

        req = urllib.request.Request(
            upload_url,
            data=data,
            method="PUT",
            headers={
                "Authorization": f"Bearer {self._blob_key}",
                "Content-Type": _CONTENT_TYPES.get(ext, "application/octet-stream"),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status >= 300:
                    raise UploadError(f"blob upload failed: HTTP {resp.status}")
        except UploadError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Embr blob upload failed")
            raise UploadError(f"blob upload failed: {e}") from e

        # Same-domain relative URL — served publicly by the Embr proxy.
        return key, url_path


def is_embr_blob_enabled() -> bool:
    return bool(os.getenv("EMBR_BLOB_KEY"))


def _make_store():
    """Pick a blob backend based on the environment.

    On Embr `EMBR_BLOB_KEY` is auto-injected, so the Embr backend is the
    default in production. Locally we save uploads to disk so the app works
    without any setup.
    """
    blob_key = os.getenv("EMBR_BLOB_KEY")
    if blob_key:
        domain = os.getenv("EMBR_DOMAIN") or os.getenv("EMBR_ENV_DOMAIN")
        logger.info(
            "Using Embr managed blob storage (domain=%s)",
            domain or "<unset — falling back to localhost>",
        )
        return _EmbrBlobStore(blob_key, domain)
    logger.info("Using local blob storage at %s", UPLOADS_DIR)
    return _LocalStore()


store = _make_store()
