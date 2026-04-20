"""WSGI entrypoint for Oryx/Gunicorn.

Wraps the FastAPI (ASGI) app with a2wsgi so gunicorn's default sync
workers can serve it — no special worker class or startCommand needed.
Oryx auto-detects this file and launches `gunicorn application:app`.
"""

from a2wsgi import ASGIMiddleware
from backend.app import app as _asgi_app

app = ASGIMiddleware(_asgi_app)
