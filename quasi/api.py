"""HTTP API. Run: uvicorn quasi.api:app --reload

Write actions are declined over HTTP until the approvals queue lands (Phase 2):
the agent tells you it couldn't do them, and you can approve from the CLI.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agents import Agent, build_agent, run_brief
from .agents.approvals import deny_all
from .integrations.google_auth import GoogleAuthError

app = FastAPI(title="Quasi")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    stop_reason: str
    tool_calls: list[dict]


@lru_cache(maxsize=1)
def agent() -> Agent:
    return build_agent(approver=deny_all, interactive_auth=False)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    try:
        res = agent().ask(req.question)
    except GoogleAuthError as e:
        raise HTTPException(503, str(e))
    return AskResponse(answer=res.text, stop_reason=res.stop_reason, tool_calls=res.tool_calls)


class BriefResponse(BaseModel):
    brief: str
    saved_to: str
    stop_reason: str


@app.post("/brief", response_model=BriefResponse)
def brief():
    """Generate today's brief (what the 7am scheduler will call). Never emails from HTTP."""
    try:
        b, path, _ = run_brief(interactive_auth=False, email=False)
    except GoogleAuthError as e:
        raise HTTPException(503, str(e))
    return BriefResponse(brief=b.text, saved_to=str(path), stop_reason=b.stop_reason)
