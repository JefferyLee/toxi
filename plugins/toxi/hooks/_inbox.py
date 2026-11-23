"""Hand peer messages to the agent without losing any (shared by the hooks).

`toxi peer-inbox --peek` lists the messages for this session and marks none of
them read. The hook prints them for the agent, and only then marks them read.
A hook that is cut off before that leaves them unread, so they come again at
the next hook: twice is better than never. Each toxi call has a short timeout,
so the whole hook stays inside the agent's hook timeout.
"""
from __future__ import annotations  # hooks may run on an old system python3

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

STEP_TIMEOUT = 3.5  # seconds per toxi call; two calls stay under an 8 s hook timeout

_BREAK = re.compile(r"\r\n|[\n\r\x0b\x0c\x85\u2028\u2029]")


def indent(text: str) -> str:
    """Someone else's text with every line after the first indented by four spaces,
    so that no line of it can pass for a line the hook wrote."""
    return "\n    ".join(_BREAK.split(text))


def is_subagent(hook_input: dict) -> bool:
    """A subagent's tool call: its mail is for the main session, which answers it."""
    return bool(hook_input.get("agent_id"))


def looked() -> None:
    """Note that this session looked at the inbox now (peer_check.sh compares)."""
    seen = os.environ.get("TOXI_SEEN")
    if seen:
        try:
            with open(seen, "a"):
                os.utime(seen)
        except OSError:
            pass


def peek(toxi: str, cwd: str | None) -> tuple[str, list]:
    """(text for the agent, ids to mark read once the text is out)."""
    args = [toxi, "peer-inbox", "--peek"] + (["--cwd", cwd] if cwd else [])
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=STEP_TIMEOUT)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "", []
    if out.returncode == 2 and "--peek" in out.stderr:  # a toxi older than this plugin
        args.remove("--peek")
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=STEP_TIMEOUT)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return "", []
        return (out.stdout.strip() if out.returncode == 0 else ""), []
    if out.returncode != 0:
        return "", []
    try:
        res = json.loads(out.stdout)
    except ValueError:
        return "", []
    return res.get("text") or "", res.get("msg_uuids") or []


def mark_read(toxi: str, uuids: list) -> None:
    if not uuids:
        return
    try:  # "--": an id is never taken for an option
        subprocess.run([toxi, "mark-read", "--", *uuids], capture_output=True, timeout=STEP_TIMEOUT)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass  # still unread: delivered again next time


def blocked_before(hook_input: dict, uuids: list) -> bool:
    """For a Stop hook: did the last stop of this session block with exactly
    these messages, and the agent is still going from that block? Then they
    could not be marked read, and blocking again would loop. Records `uuids`."""
    sid = str(hook_input.get("session_id") or os.getcwd())
    path = os.path.join(tempfile.gettempdir(),
                        "toxi-stop-" + hashlib.sha256(sid.encode()).hexdigest()[:16])
    now = " ".join(sorted(uuids))
    try:
        with open(path) as f:
            before = f.read()
    except OSError:
        before = ""
    try:
        with open(path, "w") as f:
            f.write(now)
    except OSError:
        pass
    return bool(hook_input.get("stop_hook_active")) and before == now


def emit(obj: dict) -> None:
    """Print the hook's JSON answer now, before the messages are marked read."""
    print(json.dumps(obj))
    sys.stdout.flush()
