"""GET /api/stats: the sniper feed's public track record, proxied from STATS_URL (the stats.json
the VM publishes hourly) and cached on Vercel's CDN for 5 minutes. Optional JOIN_URL is passed
through for the page's call to action."""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

CACHE_OK = "public, s-maxage=300, stale-while-revalidate=600"
CACHE_ERR = "public, s-maxage=30"


def load(url: str, timeout: float = 10) -> dict:
    if not url.startswith("https://"):
        raise ValueError("STATS_URL must be an https:// URL")
    req = urllib.request.Request(url, headers={"user-agent": "track-record/1.0", "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read(5_000_000))
    if not isinstance(body, dict) or "alerts" not in body:
        raise ValueError("not a sniper feed stats.json")
    return body


def respond(handler: BaseHTTPRequestHandler, status: int, payload: dict, cache: str) -> None:
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", cache)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = load(os.environ.get("STATS_URL", ""))
        except Exception as e:  # never leak a traceback; say what's wrong
            respond(self, 502, {"error": f"track record unavailable ({type(e).__name__})"}, CACHE_ERR)
            return
        join = os.environ.get("JOIN_URL", "")
        body["join_url"] = join if join.startswith("https://") else ""
        respond(self, 200, body, CACHE_OK)
