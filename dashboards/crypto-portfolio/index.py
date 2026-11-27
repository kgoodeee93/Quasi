"""Vercel entrypoint. Vercel looks for an ASGI `app` in index.py at the project root; the real
app lives in the app package (uvicorn app.main:app locally, unchanged)."""
from app.main import app  # noqa: F401
