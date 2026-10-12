#!/usr/bin/env python3
"""Claude Code Stop hook for toxi.

If a peer agent (config.toml [agents] peers) has messaged us, blocks the stop
and feeds those messages back to Claude so it keeps working on them. Otherwise
peeks at unread messages and prints a one-line notification, silent when there
is nothing new.

Set TOXI_BIN if `toxi` isn't on PATH, and TOXI_HOME for a non-default config dir.
"""
import json
import os
import subprocess
import sys


def peer_block(toxi: str) -> bool:
    """Deliver peer-agent messages by blocking the stop. Returns True if it did."""
    args = [toxi, "peer-inbox"]
    # The dir the session was started in; it stays put when the session cd's around.
    cwd = os.environ.get("CLAUDE_PROJECT_DIR")
    if not cwd and not sys.stdin.isatty():
        try:
            cwd = json.load(sys.stdin).get("cwd")
        except (ValueError, AttributeError):
            pass
    if cwd:
        args += ["--cwd", cwd]  # lets [agents.dirs] route peers to one session
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=8)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    text = out.stdout.strip() if out.returncode == 0 else ""
    if text:
        print(json.dumps({"decision": "block", "reason": text}))
    return bool(text)


def main() -> None:
    toxi = os.environ.get("TOXI_BIN", "toxi")
    if peer_block(toxi):
        return
    try:
        out = subprocess.run(
            [toxi, "--json", "unread"], capture_output=True, text=True, timeout=8
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    if out.returncode != 0:
        return
    try:
        msgs = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return
    if not msgs:
        return

    counts: dict[str, int] = {}
    media: dict[str, int] = {}
    emoji = {"image": "📷", "voice": "🎤", "video": "🎬", "file": "📎"}
    for m in msgs:
        counts[m["alias"]] = counts.get(m["alias"], 0) + 1
        t = m.get("msg_type", "text")
        if t in emoji:
            media[t] = media.get(t, 0) + 1
    summary = ", ".join(f"{alias}({n})" for alias, n in counts.items())
    line = f"[toxi] {len(msgs)} unread from {summary}"
    if media:
        line += " (" + " ".join(f"{emoji[t]}{n}" for t, n in media.items()) + ")"
    print(line + " — /toxi:read to read (keeps your main session clean)")


if __name__ == "__main__":
    main()
    sys.exit(0)
