"""Agent sessions that share this identity, so each peer message goes to one.

A Claude Code or Codex session registers here the first time it asks for its
peer messages (hooks, the MCP server). The key is the pid of its agent process;
the entry holds the dir the session was started in and a name that is unique
among the live sessions (ignoring case): $TOXI_SESSION, else the dir name, with -2, -3 ...
added when another live session has that name. A session ends when its agent
process ends. One JSON file per session in <TOXI_HOME>/sessions/.
"""
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import paths

# Processes between toxi and the agent: the hook's shell, the hook script, toxi.
_WRAPPERS = {"sh", "bash", "zsh", "dash", "fish", "env", "toxi", "uv", "uvx"}


def _dir() -> Path:
    return paths.config_dir() / "sessions"


def _ps(pid: int):
    """(parent pid, command name) of `pid`, or None."""
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout.split(None, 1)
        return int(out[0]), os.path.basename(out[1].strip()).lstrip("-").lower()
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def agent_pid() -> int:
    """The pid of the agent process (claude, codex) that started this toxi process."""
    pid = os.getppid()
    for _ in range(8):
        info = _ps(pid)
        if info is None or pid <= 1:
            break
        ppid, comm = info
        if comm not in _WRAPPERS and not comm.startswith("python"):
            return pid
        pid = ppid
    return os.getppid()


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def live() -> list:
    """Live sessions, oldest first. Removes the files of sessions that ended."""
    out = []
    for f in _dir().glob("*.json"):
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if _alive(int(s.get("pid") or 0)):
            out.append(s)
        else:
            f.unlink(missing_ok=True)
    return sorted(out, key=lambda s: s.get("since", 0))


def current(pid: int | None = None) -> dict | None:
    """This session's entry, if it registered. Never registers."""
    try:
        return json.loads((_dir() / f"{pid or agent_pid()}.json").read_text())
    except (OSError, ValueError):
        return None


def register(cwd: str, pid: int | None = None) -> dict:
    """This session's entry; registers it (or moves it to `cwd`) if needed."""
    pid = pid or agent_pid()
    cwd = str(Path(cwd).expanduser().resolve())
    _dir().mkdir(parents=True, exist_ok=True)
    with open(_dir() / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        base = os.environ.get("TOXI_SESSION") or os.path.basename(cwd) or "session"
        base = re.sub(r"[^\w.-]+", "-", base)[:60]
        mine = current(pid)
        if mine and mine.get("cwd") == cwd and mine.get("base") == base:
            return mine
        taken = {s["name"].lower() for s in live() if s["pid"] != pid}
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            name = f"{base}-{n}"
        mine = {"pid": pid, "cwd": cwd, "base": base, "name": name, "since": time.time()}
        (_dir() / f"{pid}.json").write_text(json.dumps(mine))
        return mine
