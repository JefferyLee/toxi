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


def codex_sid(hook_input: dict) -> str | None:
    """The Codex thread id, when Codex runs the session in its shared daemon
    (the hook's parent): toxi then tells the sessions in it apart by thread."""
    sid = hook_input.get("session_id")
    if not sid:
        return None
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(os.getppid())],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return sid if " app-server" in cmd and "--listen" in cmd else None


def start_waker(toxi: str) -> None:
    """Start toxi's waker for the Codex daemon that runs this hook, which wakes
    an idle session when mail for it arrives. A second waker exits at once."""
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    if not os.path.exists(os.path.join(home, "app-server-control", "app-server-control.sock")):
        return
    try:
        subprocess.Popen([toxi, "codex-wake", "--codex-home", home, "--agent-pid", str(os.getppid())],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def peek(toxi: str, cwd: str | None, sid: str | None = None) -> tuple[str, list]:
    """(text for the agent, ids to mark read once the text is out)."""
    text, uuids, _ = peek_full(toxi, cwd, sid)
    return text, uuids


def peek_full(toxi: str, cwd: str | None, sid: str | None = None) -> tuple[str, list, bool]:
    """peek, plus whether this mail should wake an idle session (a toxi older
    than this plugin cannot say: then yes)."""
    args = [toxi, "peer-inbox", "--peek"] + (["--cwd", cwd] if cwd else [])
    try:
        out = subprocess.run(args + (["--session-id", sid] if sid else []),
                             capture_output=True, text=True, timeout=STEP_TIMEOUT)
        if sid and out.returncode == 2 and "--session-id" in out.stderr:  # a toxi older than this plugin
            out = subprocess.run(args, capture_output=True, text=True, timeout=STEP_TIMEOUT)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "", [], False
    if out.returncode == 2 and "--peek" in out.stderr:  # a toxi older than this plugin
        args.remove("--peek")
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=STEP_TIMEOUT)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return "", [], False
        return (out.stdout.strip() if out.returncode == 0 else ""), [], True
    if out.returncode != 0:
        return "", [], False
    try:
        res = json.loads(out.stdout)
    except ValueError:
        return "", [], False
    return res.get("text") or "", res.get("msg_uuids") or [], bool(res.get("wake", True))


def mark_read(toxi: str, uuids: list, sid: str | None = None) -> None:
    """Mark them read, naming the session that took them (`sid`: as for peek)."""
    if not uuids:
        return
    opt = ["--session-id", sid] if sid else []
    try:  # "--": an id is never taken for an option
        out = subprocess.run([toxi, "mark-read", *opt, "--", *uuids], capture_output=True,
                             text=True, timeout=STEP_TIMEOUT)
        if opt and out.returncode == 2 and "--session-id" in out.stderr:  # a toxi older than this plugin
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
