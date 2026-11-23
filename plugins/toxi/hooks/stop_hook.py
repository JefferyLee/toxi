#!/usr/bin/env python3
"""Codex Stop hook for toxi.

If a peer agent (config.toml [agents] peers) has messaged us, blocks the stop
and feeds those messages back to Codex so it keeps working on them. Otherwise
emits Codex Stop hook JSON with the current `toxi statusline` summary.

Hooks don't see the MCP server's env, so when TOXI_HOME isn't set we reuse the
one configured for the toxi MCP server in Codex's config.toml.
"""
from __future__ import annotations  # hooks may run on an old system python3

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _inbox  # noqa: E402


def codex_toxi_home() -> str | None:
    """TOXI_HOME from [mcp_servers.toxi.env] in Codex's config.toml, if any."""
    try:
        import tomllib
        cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        with open(cfg, "rb") as f:
            return tomllib.load(f)["mcp_servers"]["toxi"]["env"]["TOXI_HOME"]
    except Exception:
        return None


def run(toxi: str, *args: str) -> str:
    try:
        out = subprocess.run(
            [toxi, *args], capture_output=True, text=True, timeout=8
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def main() -> None:
    if "TOXI_HOME" not in os.environ:
        home = codex_toxi_home()
        if home:
            os.environ["TOXI_HOME"] = os.path.expanduser(home)
    toxi = os.environ.get("TOXI_BIN", "toxi")
    data = {}
    if not sys.stdin.isatty():
        try:
            data = json.load(sys.stdin)
        except (ValueError, AttributeError):
            pass
    # Codex runs hooks in the session's dir. With --cwd only this session's
    # messages come, not those of every session on the identity.
    sid = _inbox.codex_sid(data)
    if sid:
        _inbox.start_waker(toxi)  # in case it stopped
    text, uuids = _inbox.peek(toxi, data.get("cwd") or os.getcwd(), sid)
    if text and _inbox.blocked_before(data, uuids):
        text = ""  # the same messages again: they will not mark read, so do not loop
    if text:
        _inbox.emit({"decision": "block", "reason": text})
    _inbox.mark_read(toxi, uuids, sid)
    if text:
        return

    status = run(toxi, "statusline")
    if status:
        print(
            json.dumps(
                {
                    "continue": True,
                    "suppressOutput": True,
                    "systemMessage": status,
                }
            )
        )


if __name__ == "__main__":
    main()
