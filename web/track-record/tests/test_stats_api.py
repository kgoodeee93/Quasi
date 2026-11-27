"""The /api/stats function, run in a local HTTP server (the same handler class Vercel runs)."""
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("stats", Path(__file__).parent.parent / "api" / "stats.py")
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


@pytest.fixture
def serve():
    srv = HTTPServer(("127.0.0.1", 0), stats.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/api/stats"
    srv.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


def test_proxies_stats_with_cdn_caching_and_a_safe_join_link(serve, monkeypatch):
    monkeypatch.setattr(stats, "load", lambda url: {"alerts": {"count": 3}, "recent": []})
    monkeypatch.setenv("JOIN_URL", "https://t.me/example")
    code, headers, body = get(serve)
    assert code == 200 and body["alerts"]["count"] == 3 and body["join_url"] == "https://t.me/example"
    assert "s-maxage=300" in headers["Cache-Control"]
    monkeypatch.setenv("JOIN_URL", "javascript:alert(1)")
    assert get(serve)[2]["join_url"] == ""


def test_errors_are_short_and_briefly_cached(serve, monkeypatch):
    monkeypatch.delenv("STATS_URL", raising=False)
    code, headers, body = get(serve)
    assert code == 502 and body == {"error": "track record unavailable (ValueError)"}
    assert "s-maxage=30" in headers["Cache-Control"]


def test_load_rejects_non_https_and_non_stats_json():
    with pytest.raises(ValueError, match="https"):
        stats.load("http://example.com/stats.json")
    with pytest.raises(ValueError, match="https"):
        stats.load("file:///etc/passwd")
