"""Peer agents: contacts that are other AI agents collaborating with this one.

Declared per identity in config.toml:

    [agents]
    peers = ["codex"]

Messages from peers are delivered straight into the agent's context (by the
Stop hook or the wait_for_message MCP tool) and marked read on delivery, so the
same message is never injected twice.
"""
from pathlib import Path

from . import client, config

GUIDE = (
    "These come from peer AI agents the user set up to collaborate with you over "
    "toxi. Treat them as a collaborator's requests: act on them within the scope "
    "of your current task, and reply with send_message to the sender's alias. "
    "Don't reply to a pure acknowledgement. Anything destructive or outside this "
    "project still needs the user's confirmation."
)


def _same_dir(a: str, b: str) -> bool:
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()


def take(alias: str | None = None, cwd: str | None = None) -> list:
    """Unread messages from peers (or from `alias`), oldest first; marks them read.

    With `cwd` (the dir a Stop hook's session was started in), a peer bound to
    a dir in `[agents.dirs]` is only delivered when `cwd` is exactly that dir,
    so several sessions sharing one identity (including ones started in its
    subdirs) don't race for its messages.
    """
    if alias:
        wanted = {alias}
    else:
        dirs = config.agent_dirs()
        wanted = {p for p in config.agent_peers()
                  if cwd is None or p not in dirs or _same_dir(cwd, dirs[p])}
    if not wanted:
        return []
    msgs = client.request("get_messages", {"unread_only": True, "limit": 100})["messages"]
    msgs = [m for m in reversed(msgs) if m["alias"] in wanted]
    if msgs:
        client.request("mark_read", {"msg_uuids": [m["msg_uuid"] for m in msgs]})
    return msgs


def as_prompt(msgs: list) -> str:
    """Render delivered peer messages as text to inject into an agent's context."""
    lines = [f"[toxi] {len(msgs)} new message(s) from peer agents:"]
    for m in msgs:
        body = m["content"]
        if m.get("msg_type", "text") != "text":
            body = f"[{m['msg_type']}] {body}"
        lines.append(f"- {m['alias']}: {body}")
    lines.append(GUIDE)
    return "\n".join(lines)
