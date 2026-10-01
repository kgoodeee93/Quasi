"""HTTP API. Run: uvicorn quasi.api:app --reload

Write actions are declined over HTTP until the approvals queue lands (Phase 2):
the agent tells you it couldn't do them, and you can approve from the CLI.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agents import Agent, build_agent
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
