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

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# OpenAI Python SDK (official)
from openai import OpenAI

# ----------------------------
# Config
# ----------------------------
VAULT = Path(os.environ.get("TREECHAT_VAULT", "")).expanduser()
if not VAULT.exists():
    raise RuntimeError("Set TREECHAT_VAULT to your Obsidian vault path (TREECHAT_VAULT).")

ROOT = VAULT / "TreeChat"
BRANCH_DIR = ROOT / "branches"
ARTIFACT_DIR = ROOT / "artifacts"

BRANCH_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

MODEL = os.environ.get("TREECHAT_MODEL", "gpt-4o-mini")
SYSTEM_PROMPT = os.environ.get(
    "TREECHAT_SYSTEM_PROMPT",
    "You are a helpful assistant. Be concise, clear, and correct.",
)

client = OpenAI()

MSG_HEADER_RE = re.compile(r"^##\s+M(\d+)\s+\((User|Assistant)\)\s*$", re.M)


# ----------------------------
# Helpers: markdown frontmatter
# ----------------------------
def _read_branch_file(path: Path) -> Tuple[Dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].lstrip("\n")
            return meta, body
    return {}, text


def _write_branch_file(path: Path, meta: Dict[str, Any], body: str) -> None:
    # Keep keys stable order in output
    front = "---\n" + yaml.safe_dump(meta, sort_keys=False).strip() + "\n---\n\n"
    path.write_text(front + body.strip() + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ----------------------------
# Parsing: branch transcript
# ----------------------------
def parse_messages(body: str) -> List[Dict[str, Any]]:
    """
    Parse messages from branch markdown body:
      ## M1 (User)
      text...
      ## M2 (Assistant)
      ...
    """
    matches = list(MSG_HEADER_RE.finditer(body))
    messages: List[Dict[str, Any]] = []
    for i, m in enumerate(matches):
        m_no = int(m.group(1))
        role = "user" if m.group(2).lower() == "user" else "assistant"
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip("\n").strip()
        messages.append({"m": m_no, "role": role, "content": content})
    return messages


def append_message(branch_id: str, role: str, content: str) -> Dict[str, Any]:
    path = BRANCH_DIR / f"{branch_id}.md"
    if not path.exists():
        raise HTTPException(404, f"Branch not found: {branch_id}")

    meta, body = _read_branch_file(path)
    msgs = parse_messages(body)
    next_m = (msgs[-1]["m"] + 1) if msgs else 1

    header = f"## M{next_m} ({'User' if role=='user' else 'Assistant'})\n"
    addition = header + content.strip() + "\n\n"
    body = (body.rstrip() + "\n\n" + addition).lstrip("\n")

    # keep children cache optional; we update only on create/fork
    _write_branch_file(path, meta, body)
    return {"m": next_m, "role": role, "content": content}


# ----------------------------
# Storage adapter (Option A)
# ----------------------------
def list_branches() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in sorted(BRANCH_DIR.glob("*.md")):
        meta, _body = _read_branch_file(f)
        meta = meta or {}
        # normalize
        meta.setdefault("branch_id", f.stem)
        meta.setdefault("title", f.stem)
        meta.setdefault("parent_branch_id", "")
        meta.setdefault("fork_from_message", "")
        meta.setdefault("created_at", "")
        out.append(meta)
    return out


def get_branch(branch_id: str) -> Dict[str, Any]:
    path = BRANCH_DIR / f"{branch_id}.md"
    if not path.exists():
        raise HTTPException(404, f"Branch not found: {branch_id}")
    meta, body = _read_branch_file(path)
    msgs = parse_messages(body)
    return {"meta": meta, "messages": msgs}


def create_branch(
    title: str,
    parent_branch_id: str = "",
    fork_from_message: Optional[int] = None,
) -> Dict[str, Any]:
    branch_id = f"b_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    meta: Dict[str, Any] = {
        "branch_id": branch_id,
        "title": title or branch_id,
        "parent_branch_id": parent_branch_id or "",
        "fork_from_message": (int(fork_from_message) if fork_from_message else ""),
        "created_at": _now_iso(),
        # Optional summarization checkpoint fields (unused in v0, but supported)
        # "context_summary": "",
        # "summary_covers_up_to_message": "",
    }
    path = BRANCH_DIR / f"{branch_id}.md"
    _write_branch_file(path, meta, "")
    # ensure artifact folder exists for this branch
    (ARTIFACT_DIR / branch_id).mkdir(parents=True, exist_ok=True)
    return meta


def delete_branch(branch_id: str, delete_artifacts: bool = True) -> None:
    path = BRANCH_DIR / f"{branch_id}.md"
    if not path.exists():
        raise HTTPException(404, f"Branch not found: {branch_id}")

    # v0: delete subtree (children) as well
    branches = list_branches()
    children = [b["branch_id"] for b in branches if (b.get("parent_branch_id") or "") == branch_id]
    for child_id in children:
        delete_branch(child_id, delete_artifacts=delete_artifacts)

    path.unlink()
    if delete_artifacts:
        art_dir = ARTIFACT_DIR / branch_id
        if art_dir.exists():
            for p in art_dir.glob("**/*"):
                if p.is_file():
                    p.unlink()
            # remove empty dirs
            for p in sorted(art_dir.glob("**/*"), reverse=True):
                if p.is_dir():
                    try:
                        p.rmdir()
                    except OSError:
                        pass
            try:
                art_dir.rmdir()
            except OSError:
                pass


# ----------------------------
# Context building
# ----------------------------
def _apply_summary_checkpoint(meta: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Optional: if frontmatter contains:
      context_summary: "..."
      summary_covers_up_to_message: 40
    then replace messages up to that point with one summary message.
    """
    summary = (meta.get("context_summary") or "").strip()
    covers = meta.get("summary_covers_up_to_message") or ""
    if not summary or not covers:
        return messages
    try:
        covers_n = int(covers)
    except Exception:
        return messages

    remaining = [m for m in messages if m["m"] > covers_n]
    # Put summary as a system message-like assistant message
    summary_msg = {"m": covers_n, "role": "assistant", "content": f"(Summary)\n{summary}"}
    return [summary_msg] + remaining


def build_context(branch_id: str) -> List[Dict[str, str]]:
    """
    Build context array for OpenAI:
      context(B) = context(parent(B) up to fork point) + messages(B)
    Where parent transcript is cut at fork_from_message (inclusive).
    """
    data = get_branch(branch_id)
    meta: Dict[str, Any] = data["meta"] or {}
    my_msgs = _apply_summary_checkpoint(meta, data["messages"])

    parent_id = (meta.get("parent_branch_id") or "").strip()
    fork_from = meta.get("fork_from_message")

    if not parent_id:
        combined = my_msgs
    else:
        parent = get_branch(parent_id)
        parent_meta = parent["meta"] or {}
        parent_msgs = _apply_summary_checkpoint(parent_meta, parent["messages"])

        if fork_from:
            try:
                cutoff = int(fork_from)
                parent_msgs = [m for m in parent_msgs if m["m"] <= cutoff]
            except Exception:
                pass

        # Recurse up: if parent is itself forked, include its ancestry too
        ancestry = build_context(parent_id)
        # ancestry already includes parent messages fully; we need to cut it at cutoff
        # simplest: rebuild ancestry from files by recursion on raw messages instead:
        # We'll do recursion on meta/messages, not on already-converted contexts.
        # So implement helper to return message dicts.
        combined_parent_msgs = build_message_chain(parent_id)
        if fork_from:
            try:
                cutoff = int(fork_from)
                combined_parent_msgs = [m for m in combined_parent_msgs if m["m_global"] <= cutoff and m["origin_branch"] == parent_id] \
                                      + [m for m in combined_parent_msgs if m["origin_branch"] != parent_id]
            except Exception:
                pass

        # However the above m_global logic is messy; keep v0 simple:
        # We only support cutting the immediate parent branch at fork point and include its own ancestry fully.
        # That is: ancestry(parent) already has correct context for parent; we then trim to the parent cutoff by
        # trimming based on parent branch local numbering not available in converted context.
        # So for v0 we limit to single-level forks (works great for the “clarify a word” use case).
        #
        # If you want multi-level correctness later, store global step IDs or embed origin info in context objects.
        #
        # v0 implementation: single-level forks.
        combined = parent_msgs + my_msgs

    # Convert to OpenAI role-based messages; include a pinned system prompt
    out: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in combined:
        if not m.get("content"):
            continue
        out.append({"role": m["role"], "content": m["content"]})
    return out


def build_message_chain(branch_id: str) -> List[Dict[str, Any]]:
    """
    (Reserved for v1 multi-level accurate chaining)
    """
    data = get_branch(branch_id)
    meta: Dict[str, Any] = data["meta"] or {}
    msgs = data["messages"]
    return [{"origin_branch": branch_id, "m_global": m["m"], **m} for m in msgs]


# ----------------------------
# OpenAI call
# ----------------------------
def call_chatgpt(messages: List[Dict[str, str]]) -> str:
    """
    Uses Responses API via official SDK.
    """
    resp = client.responses.create(
        model=MODEL,
        input=messages,
    )
    return resp.output_text.strip()


# ----------------------------
# FastAPI
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
    meta = create_branch(req.title, parent_branch_id="", fork_from_message=None)
    return meta


@app.get("/api/branches")
def api_branches():
    return list_branches()


@app.get("/api/branch/{branch_id}")
def api_branch(branch_id: str):
    return get_branch(branch_id)


class ReplyReq(BaseModel):
    branch_id: str
    user_text: str


@app.post("/api/reply")
def api_reply(req: ReplyReq):
    # Append user message
    append_message(req.branch_id, "user", req.user_text)

    # Build context and call model
    ctx = build_context(req.branch_id)
    assistant_text = call_chatgpt(ctx)

    # Append assistant message
    assistant = append_message(req.branch_id, "assistant", assistant_text)
    return {"assistant": assistant}


class ForkReq(BaseModel):
    from_branch_id: str
    from_message: int
    title: str


@app.post("/api/fork")
def api_fork(req: ForkReq):
    meta = create_branch(req.title, parent_branch_id=req.from_branch_id, fork_from_message=req.from_message)
    # Optional breadcrumb in new branch
    breadcrumb = f"Forked from [[{req.from_branch_id}]] at M{req.from_message}."
    append_message(meta["branch_id"], "user", breadcrumb)
    return meta


class DeleteReq(BaseModel):
    branch_id: str
    delete_artifacts: bool = True


@app.post("/api/delete_branch")
def api_delete_branch(req: DeleteReq):
    delete_branch(req.branch_id, delete_artifacts=req.delete_artifacts)
    return {"ok": True}
