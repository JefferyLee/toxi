#!/usr/bin/env python3
"""Claude Code idle wake-up for toxi (design §13.9).

An idle session runs no hook, so it does not see a peer message until the user
types. This waiter fills that gap. SessionStart and Stop start it in the
background (`async` + `asyncRewake`). It watches the daemon's inbox.flag. When
messages for this session arrive, it prints them to stderr, marks them read and
exits 2: Claude Code then starts a turn with them. UserPromptSubmit runs it
with --disarm, because a busy session gets its messages from the PostToolUse
and Stop hooks. One waiter per session: a new one ends the one before.

Two agents that answer each other could wake each other without end. So a
session wakes at most WAKE_LIMIT times between two prompts of the user; after
that its mail waits for the user, as it did before this hook.

Set TOXI_BIN if `toxi` isn't on PATH, and TOXI_HOME for a non-default config dir.
"""
from __future__ import annotations  # hooks may run on an old system python3

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _inbox  # noqa: E402

POLL = float(os.environ.get("TOXI_WAKE_POLL", "1"))  # seconds between inbox.flag checks
# The first look waits until the event's own hooks (SessionStart, Stop) have
# delivered what was already there, so the two do not hand over the same mail.
SETTLE = float(os.environ.get("TOXI_WAKE_SETTLE", "5"))
WAKE_LIMIT = int(os.environ.get("TOXI_WAKE_LIMIT", "20"))


def _path(key: str, kind: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"toxi-wake-{key}.{kind}")


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _stop_previous(pidf: str) -> None:
    """End the earlier waiter of this session, if it is still ours."""
    try:
        old = int(_read(pidf))
    except ValueError:
        return
    if old == os.getpid():
        return
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(old)],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return
    if "wake_hook" in cmd:
        try:
            os.kill(old, signal.SIGTERM)
        except OSError:
            pass


def main() -> int:
    data = {}
    if not sys.stdin.isatty():
        try:
            data = json.load(sys.stdin)
        except (ValueError, OSError):
            pass
    if _inbox.is_subagent(data):
        return 0
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    key = hashlib.sha256(str(data.get("session_id") or cwd).encode()).hexdigest()[:16]
    pidf, wakes = _path(key, "pid"), _path(key, "wakes")
    _stop_previous(pidf)
    if "--disarm" in sys.argv:  # the user is here: busy now, and the count starts again
        for path in (pidf, wakes):
            try:
                os.unlink(path)
            except OSError:
                pass
        return 0
    try:
        count = int(_read(wakes) or 0)
    except ValueError:
        count = 0
    if count >= WAKE_LIMIT:
        return 0  # woken often enough without the user: let the mail wait for them
    with open(pidf, "w") as f:
        f.write(str(os.getpid()))

    toxi = os.environ.get("TOXI_BIN", "toxi")
    flag = os.path.join(os.environ.get("TOXI_HOME") or os.path.expanduser("~/.config/toxi"),
                        "inbox.flag")
    woke = _path(key, "woke")  # ids of the last wake: a batch that will not mark read wakes once
    seen, first = _mtime(flag), time.time() + SETTLE
    while _read(pidf) == str(os.getpid()):  # else replaced or disarmed
        now = _mtime(flag)
        if now != seen or (first and time.time() >= first):
            seen, first = now, 0
            text, uuids = _inbox.peek(toxi, cwd)
            batch = " ".join(sorted(uuids))
            if text and batch != _read(woke):
                with open(woke, "w") as f:
                    f.write(batch)
                with open(wakes, "w") as f:
                    f.write(str(count + 1))
                sys.stderr.write(text)
                sys.stderr.flush()
                _inbox.mark_read(toxi, uuids)
                return 2  # asyncRewake: Claude Code starts a turn with stderr
            _inbox.mark_read(toxi, uuids)  # acks, or the batch woken for before
        time.sleep(POLL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
