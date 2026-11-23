#!/usr/bin/env python3
"""Codex SessionStart hook for toxi: deliver peer-agent messages routed to this
project as plain stdout (Codex adds it as developer context), so a session that
starts or resumes picks up where the peers left off.

Set TOXI_BIN if `toxi` isn't on PATH; TOXI_HOME is taken from Codex's own
[mcp_servers.toxi] env when not set, like the Stop hook does.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _inbox  # noqa: E402


def codex_toxi_home():
    try:
        import tomllib
        cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        with open(cfg, "rb") as f:
            return tomllib.load(f)["mcp_servers"]["toxi"]["env"]["TOXI_HOME"]
    except Exception:
        return None


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
    sid = _inbox.codex_sid(data)
    text, uuids = _inbox.peek(toxi, data.get("cwd") or os.getcwd(), sid)
    if sid:
        _inbox.start_waker(toxi)
    if text:
        print(text)
        sys.stdout.flush()
    _inbox.mark_read(toxi, uuids, sid)  # after the text is out


if __name__ == "__main__":
    main()
    sys.exit(0)
