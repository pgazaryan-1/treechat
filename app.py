"""
TreeChat v0 — Obsidian Option A + Local Web UI

- Stores each branch as a Markdown note under <VAULT>/TreeChat/branches/<branch_id>.md
- Forks reference parent context up to a parent message number; parent history is NOT copied.
- On reply, builds context = ancestor(parent up to fork) + current branch messages + new user msg
- Minimal UI: tree (branches) + chat transcript + fork from message number

Run:
  pip install -r requirements.txt
  export TREECHAT_VAULT="/absolute/path/to/YourObsidianVault"
  export OPENAI_API_KEY="..."
  uvicorn app:app --reload --port 8787

Open:
  http://localhost:8787
"""

import os
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

# Import storage module
import storage as st

# ----------------------------
# Config
# ----------------------------
VAULT = Path(os.environ.get("TREECHAT_VAULT", "")).expanduser()
if not VAULT.exists():
    raise RuntimeError("Set TREECHAT_VAULT to your Obsidian vault path (TREECHAT_VAULT).")

MODEL = os.environ.get("TREECHAT_MODEL", "gpt-4o-mini")
SYSTEM_PROMPT = os.environ.get(
    "TREECHAT_SYSTEM_PROMPT",
    "You are a helpful assistant. Be concise, clear, and correct.",
)

client = OpenAI()

# ----------------------------
# OpenAI call
# ----------------------------
def call_chatgpt(messages: List[Dict[str, str]]) -> str:
    """
    Uses Responses API via official SDK.
    """
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
    )
    return resp.choices[0].message.content.strip()

# ----------------------------
# FastAPI App
# ----------------------------
app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

class CreateRootReq(BaseModel):
    title: str = "Root"

@app.post("/api/create_root")
def api_create_root(req: CreateRootReq):
    meta = st.create_branch(req.title, parent_branch_id="", fork_from_message=None)
    return meta

@app.get("/api/branches")
def api_branches():
    return st.list_branches()

@app.get("/api/branch/{branch_id}")
def api_branch(branch_id: str):
    return st.get_branch(branch_id)

class ReplyReq(BaseModel):
    branch_id: str
    user_text: str

@app.post("/api/reply")
def api_reply(req: ReplyReq):
    # Append user message
    st.append_message(req.branch_id, "user", req.user_text)

    # Build context and call model
    ctx = st.build_context(req.branch_id)
    # Add system prompt to context
    ctx_with_system = [{"role": "system", "content": SYSTEM_PROMPT}] + ctx
    assistant_text = call_chatgpt(ctx_with_system)

    # Append assistant message
    assistant = st.append_message(req.branch_id, "assistant", assistant_text)
    return {"assistant": assistant}

class ForkReq(BaseModel):
    from_branch_id: str
    from_message: int
    title: str

@app.post("/api/fork")
def api_fork(req: ForkReq):
    meta = st.create_branch(
        req.title, 
        parent_branch_id=req.from_branch_id, 
        fork_from_message=req.from_message
    )
    # Optional breadcrumb in new branch
    breadcrumb = f"Forked from [[{req.from_branch_id}]] at M{req.from_message}."
    st.append_message(meta["branch_id"], "user", breadcrumb)
    return meta

class DeleteReq(BaseModel):
    branch_id: str
    delete_artifacts: bool = True

@app.post("/api/delete_branch")
def api_delete_branch(req: DeleteReq):
    st.delete_branch(req.branch_id, delete_artifacts=req.delete_artifacts)
    return {"ok": True}
