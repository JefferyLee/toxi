#!/usr/bin/env python3
"""Claude Code PostToolUse hook for toxi: deliver peer-agent messages mid-turn.

Runs only when hooks/peer_check.sh saw the daemon's inbox.flag move. Delivers
unread messages from peer agents routed to this project (see `toxi peer-inbox`)
as additionalContext, so a running session picks them up at its next tool call
instead of at the end of the turn. Silent when there is nothing for us.

Set TOXI_BIN if `toxi` isn't on PATH, and TOXI_HOME for a non-default config dir.
"""
import json
import os
import subprocess
import sys


def main() -> None:
    toxi = os.environ.get("TOXI_BIN", "toxi")
    cwd = os.environ.get("CLAUDE_PROJECT_DIR")
    if not cwd and not sys.stdin.isatty():
        try:
            cwd = json.load(sys.stdin).get("cwd")
        except (ValueError, AttributeError):
            pass
    args = [toxi, "peer-inbox"] + (["--cwd", cwd] if cwd else [])
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=8)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    text = out.stdout.strip() if out.returncode == 0 else ""
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": text}}))


if __name__ == "__main__":
    main()
    sys.exit(0)
