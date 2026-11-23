#!/usr/bin/env python3
"""Claude Code PostToolUse hook for toxi: deliver peer-agent messages mid-turn.

Runs only when hooks/peer_check.sh saw the daemon's inbox.flag move. Delivers
unread messages from peer agents routed to this project (see `toxi peer-inbox`)
as additionalContext, so a running session picks them up at its next tool call
instead of at the end of the turn. Silent when there is nothing for us, and in
a subagent: the main session gets its messages.

Set TOXI_BIN if `toxi` isn't on PATH, and TOXI_HOME for a non-default config dir.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _inbox  # noqa: E402


def main() -> None:
    toxi = os.environ.get("TOXI_BIN", "toxi")
    data = {}
    if not sys.stdin.isatty():
        try:
            data = json.load(sys.stdin)
        except (ValueError, AttributeError):
            pass
    if _inbox.is_subagent(data):
        return
    _inbox.looked()
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd")
    text, uuids = _inbox.peek(toxi, cwd)
    if text:
        _inbox.emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                            "additionalContext": text}})
    _inbox.mark_read(toxi, uuids)


if __name__ == "__main__":
    main()
    sys.exit(0)
