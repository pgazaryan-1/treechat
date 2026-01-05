"""Storage models and operations for TreeChat.

This module contains the core data models and storage operations for the TreeChat application.
It handles all file I/O and data persistence logic.
"""

import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import HTTPException

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

SYSTEM_PROMPT = os.environ.get(
    "TREECHAT_SYSTEM_PROMPT",
    "You are a helpful assistant. Be concise, clear, and correct.",
)

MSG_HEADER_RE = re.compile(r"^##\s+M(\d+)\s+\((User|Assistant)\)\s*$", re.M)

# ----------------------------
# File I/O Helpers
# ----------------------------
def _read_branch_file(path: Path) -> Tuple[Dict[str, Any], str]:
    """Read branch file and return (metadata, body)."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].lstrip("\n")
            return meta, body
    return {}, text


def _write_branch_file(path: Path, meta: Dict[str, Any], body: str) -> None:
    """Write branch file with YAML frontmatter and body."""
    front = "---\n" + yaml.safe_dump(meta, sort_keys=False).strip() + "\n---\n\n"
    path.write_text(front + body.strip() + "\n", encoding="utf-8")


def _now_iso() -> str:
    """Return current time in ISO format."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ----------------------------
# Message Parsing
# ----------------------------
def parse_messages(body: str) -> List[Dict[str, Any]]:
    """
    Parse messages from branch markdown body.
    
    Example:
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
    """Append a message to a branch."""
    path = BRANCH_DIR / f"{branch_id}.md"
    if not path.exists():
        raise HTTPException(404, f"Branch not found: {branch_id}")

    meta, body = _read_branch_file(path)
    msgs = parse_messages(body)
    next_m = (msgs[-1]["m"] + 1) if msgs else 1

    header = f"## M{next_m} ({'User' if role=='user' else 'Assistant'})\n"
    addition = header + content.strip() + "\n\n"
    body = (body.rstrip() + "\n\n" + addition).lstrip("\n")

    # Update the last updated time
    meta["updated_at"] = _now_iso()
    
    _write_branch_file(path, meta, body)
    return {"m": next_m, "role": role, "content": content}


# ----------------------------
# Branch Operations
# ----------------------------
def list_branches() -> List[Dict[str, Any]]:
    """List all branches with their metadata."""
    out: List[Dict[str, Any]] = []
    for f in sorted(BRANCH_DIR.glob("*.md")):
        meta, _body = _read_branch_file(f)
        meta = meta or {}
        # Normalize metadata
        meta.setdefault("branch_id", f.stem)
        meta.setdefault("title", f.stem)
        meta.setdefault("parent_branch_id", "")
        meta.setdefault("fork_from_message", "")
        meta.setdefault("created_at", "")
        out.append(meta)
    return out


def get_branch(branch_id: str) -> Dict[str, Any]:
    """Get a single branch with its messages."""
    path = BRANCH_DIR / f"{branch_id}.md"
    if not path.exists():
        raise HTTPException(404, f"Branch not found: {branch_id}")
        
    meta, body = _read_branch_file(path)
    msgs = parse_messages(body)
    return {"meta": meta or {}, "messages": msgs}


def create_branch(
    title: str,
    parent_branch_id: str = "",
    fork_from_message: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a new branch, optionally forked from an existing one."""
    branch_id = f"b_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    meta: Dict[str, Any] = {
        "branch_id": branch_id,
        "title": title or branch_id,
        "parent_branch_id": parent_branch_id or "",
        "fork_from_message": (int(fork_from_message) if fork_from_message else ""),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    
    path = BRANCH_DIR / f"{branch_id}.md"
    _write_branch_file(path, meta, "")
    
    # Ensure artifact folder exists for this branch
    (ARTIFACT_DIR / branch_id).mkdir(parents=True, exist_ok=True)
    
    return meta


def delete_branch(branch_id: str, delete_artifacts: bool = True) -> None:
    """Delete a branch and optionally its artifacts."""
    path = BRANCH_DIR / f"{branch_id}.md"
    if not path.exists():
        raise HTTPException(404, f"Branch not found: {branch_id}")

    # Delete all children recursively
    branches = list_branches()
    children = [b["branch_id"] for b in branches if (b.get("parent_branch_id") or "") == branch_id]
    for child_id in children:
        delete_branch(child_id, delete_artifacts=delete_artifacts)

    # Delete the branch file
    path.unlink()
    
    # Delete artifacts if requested
    if delete_artifacts:
        art_dir = ARTIFACT_DIR / branch_id
        if art_dir.exists():
            # Delete all files in the artifact directory
            for p in art_dir.glob("**/*"):
                if p.is_file():
                    p.unlink()
            # Remove empty directories
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
# Context Building
# ----------------------------
def _apply_summary_checkpoint(meta: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply summary checkpoint to messages if defined in metadata.
    
    If frontmatter contains:
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
    except (ValueError, TypeError):
        return messages

    remaining = [m for m in messages if m["m"] > covers_n]
    summary_msg = {
        "m": covers_n, 
        "role": "assistant", 
        "content": f"(Summary)\n{summary}"
    }
    return [summary_msg] + remaining


def build_context(branch_id: str) -> List[Dict[str, str]]:
    """
    Build context array for OpenAI API calls.
    
    Context is built as: context(parent up to fork point) + messages(current branch)
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
            except (ValueError, TypeError):
                pass

        # For v0, we use a simple approach: parent messages up to fork point + current branch messages
        combined = parent_msgs + my_msgs

    # Convert to OpenAI format with system prompt
    out: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    out.extend(
        {"role": m["role"], "content": m["content"]}
        for m in combined
        if m.get("content")
    )
    return out


def build_message_chain(branch_id: str) -> List[Dict[str, Any]]:
    """(Reserved for v1 multi-level accurate chaining)"""
    data = get_branch(branch_id)
    meta: Dict[str, Any] = data["meta"] or {}
    msgs = data["messages"]
    return [{"origin_branch": branch_id, "m_global": m["m"], **m} for m in msgs]
