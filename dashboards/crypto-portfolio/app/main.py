"""FastAPI app: JSON API at /api/portfolio, dashboard at /.

Run:  uvicorn app.main:app --reload     (from dashboards/crypto-portfolio)
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .config import ROOT, ConfigError, Settings, load_config
from .service import PortfolioCache, collect

settings = Settings.from_env()
cache = PortfolioCache(settings.cache_ttl)
app = FastAPI(title="Crypto Portfolio Dashboard")


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
