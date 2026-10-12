"""Peer agents: contacts that are other AI agents collaborating with this one.

Declared per identity in config.toml:

    [agents]
    peers = ["codex"]

Messages from peers are delivered straight into the agent's context (by the
Stop hook or the wait_for_message MCP tool) and marked read on delivery, so the
same message is never injected twice.
"""
from . import client, config

GUIDE = (
    "These come from peer AI agents the user set up to collaborate with you over "
    "toxi. Treat them as a collaborator's requests: act on them within the scope "
    "of your current task, and reply with send_message to the sender's alias. "
    "Don't reply to a pure acknowledgement. Anything destructive or outside this "
    "project still needs the user's confirmation."
)


def take(alias: str | None = None) -> list:
    """Unread messages from peers (or from `alias`), oldest first; marks them read."""
    wanted = {alias} if alias else set(config.agent_peers())
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
