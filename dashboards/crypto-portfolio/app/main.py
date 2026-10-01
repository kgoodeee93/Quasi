"""FastAPI app: JSON API at /api/portfolio, dashboard at /.

Run:  uvicorn app.main:app --reload     (from dashboards/crypto-portfolio)
"""
from __future__ import annotations

import base64
import secrets

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .config import ROOT, ConfigError, Settings, load_config
from .service import PortfolioCache, collect

settings = Settings.from_env()
cache = PortfolioCache(settings.cache_ttl)
app = FastAPI(title="Crypto Portfolio Dashboard")


@app.middleware("http")
async def require_password(request: Request, call_next):
    """HTTP Basic auth over the whole site when DASHBOARD_PASSWORD is set (any username).

    Your browser shows its own login prompt and remembers it, which works on a phone too.
    /api/health stays open for uptime checks.
    """
    if not settings.dashboard_password or request.url.path == "/api/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            _, _, password = base64.b64decode(header[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            password = ""
        if secrets.compare_digest(password.encode(), settings.dashboard_password.encode()):
            return await call_next(request)
    return Response("Password required", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Crypto Portfolio"'})


@app.get("/api/portfolio")
async def portfolio(refresh: bool = False):
    try:
        config = load_config(settings)  # re-read each time so edits to wallets.json apply live
    except (ConfigError, ValueError) as e:
        raise HTTPException(400, f"wallets.json: {e}")
    try:
        return await cache.get(lambda: collect(config, settings), force=refresh)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        hint = " (rate limited; add COINGECKO_API_KEY or raise CACHE_TTL)" if code == 429 else ""
        raise HTTPException(502, f"Upstream error {code} from {e.request.url.host}{hint}")


@app.get("/api/health")
async def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
