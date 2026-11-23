"""Agent sessions that share this identity, so each peer message goes to one.

A Claude Code or Codex session registers here the first time it asks for its
peer messages (hooks, the MCP server). The key is the pid of its agent process;
the entry holds the dir the session was started in and a name that is unique
among the live sessions (ignoring case): $TOXI_SESSION, else the name the user
gave the Claude Code session (/rename), else the dir name, with -2, -3 ...
added when another live session has that name, or had it before a rename. The
name is checked again each time the session registers: a rename takes effect,
and a session with a suffix takes the plain name back when the session that had
it ends. A session keeps its earlier names (`former`), so replies to messages
it sent under an earlier name still find it. A session ends
when its agent process ends. One JSON file per session in <TOXI_HOME>/sessions/.

Some agents run several sessions in one process (an OpenCode server, a Cursor
window). Their sessions also carry the agent's own session id (`sid`), and the
key becomes pid + sid. Such an entry ends when its adapter has not renewed it
for SID_TTL, or says it ended (`end`), as the process itself lives on.
"""
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import paths

SID_TTL = 3600  # seconds an entry with a session id lives without being renewed
# Session ids the agents put in the environment of the commands they run.
_SID_ENV = ("TOXI_SESSION_ID", "PI_SESSION_ID", "OPENCODE_SESSION_ID")


def env_sid() -> str | None:
    for k in _SID_ENV:
        if os.environ.get(k):
            return os.environ[k]
    return codex_sid(os.environ.get("CODEX_THREAD_ID"))


GUIDE_EVERY = 7200  # the full peer guide again after this long: the agent may have compacted it away


def guided(me: dict | None, trust: str) -> bool:
    """Did this session get the full guide for `trust` lately?"""
    return bool(me) and time.time() - (me.get("guided") or {}).get(trust, 0) < GUIDE_EVERY


def note_guided(me: dict | None, trusts) -> None:
    """Record that this session got the full guide for these trust levels."""
    if not me or not trusts or me.get("transient") or "pid" not in me:
        return
    with open(_dir() / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cur = current(me["pid"], me.get("sid"))
        if not cur or cur.get("name") != me.get("name"):
            return
        cur["guided"] = dict(cur.get("guided") or {}, **{t: time.time() for t in trusts})
        _file(me["pid"], me.get("sid")).write_text(json.dumps(cur))
        me["guided"] = cur["guided"]


def set_about(text: str, sid: str | None = None) -> dict | None:
    """Set this session's one-line purpose, shown in the session directory that
    collaborator and owner peers get. None when this is not an agent session."""
    with open(_dir() / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cur = current(sid=sid)
        if not cur:
            return None
        cur["about"] = text.strip()[:120]
        if not cur["about"]:
            del cur["about"]
        _file(cur["pid"], cur.get("sid")).write_text(json.dumps(cur))
        return cur


def codex_daemon(pid: int) -> bool:
    """Is `pid` the shared Codex app-server daemon? It runs the threads of many
    sessions, and toxi's Codex waker reaches it. Not a TUI that runs its own
    thread, nor the private server of the ChatGPT app."""
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return " app-server" in cmd and "--listen" in cmd


def codex_sid(thread_id: str | None, pid: int | None = None) -> str | None:
    """A Codex thread id as the session id, for a thread in the shared daemon
    that the toxi hooks registered by its id: elsewhere, and with hooks older
    than this toxi, the process is the session, as before."""
    if not thread_id or not any(s.get("sid") == thread_id for s in live()):
        return None
    return thread_id if codex_daemon(pid or agent_pid()) else None


def _file(pid: int, sid: str | None) -> Path:
    if not sid:
        return _dir() / f"{pid}.json"
    return _dir() / f"{pid}-{re.sub(r'[^A-Za-z0-9_.-]+', '_', sid)[:80]}.json"


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
    if os.environ.get("TOXI_AGENT_PID", "").isdigit():  # set by a helper that runs detached (codex_wake)
        return int(os.environ["TOXI_AGENT_PID"])
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
        expired = s.get("sid") and time.time() - s.get("seen", 0) > SID_TTL
        if _alive(int(s.get("pid") or 0)) and not expired:
            out.append(s)
        else:
            f.unlink(missing_ok=True)
    return sorted(out, key=lambda s: s.get("since", 0))


def resolve(name: str, live: list) -> str:
    """The current name (lower case) of the live session called `name` now or
    before a rename; `name` itself when no live session has it."""
    name = name.lower()
    for s in live:
        if name in (n.lower() for n in [s["name"], *s.get("former", [])]):
            return s["name"].lower()
    return name


def _only_sid(pid: int) -> str | None:
    """The session id of the one session this process registered with an id.
    A helper the agent starts without that id in its environment (Pi starts its
    MCP servers so) is part of that session, not a second one beside it. With
    several (an OpenCode server), None: it cannot tell."""
    sids = [s["sid"] for s in live() if s["pid"] == pid and s.get("sid")]
    return sids[0] if len(sids) == 1 else None


def current(pid: int | None = None, sid: str | None = None) -> dict | None:
    """This session's entry, if it registered. Never registers."""
    pid = pid or agent_pid()
    try:
        return json.loads(_file(pid, sid or env_sid() or _only_sid(pid)).read_text())
    except (OSError, ValueError):
        return None


def end(pid: int | None = None, sid: str | None = None) -> None:
    """The adapter says this session ended (its process may live on)."""
    _file(pid or agent_pid(), sid or env_sid()).unlink(missing_ok=True)


def _claude_name(pid: int) -> str | None:
    """The name the user gave the Claude Code session with this pid, if any."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        s = json.loads((home / "sessions" / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    return s.get("name") if s.get("nameSource") == "user" else None


def register(cwd: str, pid: int | None = None, sid: str | None = None,
             name: str | None = None) -> dict:
    """This session's entry; registers it (or moves it to `cwd`) if needed.
    `name`: what the agent calls the session, if it has a name for it."""
    pid = pid or agent_pid()
    sid = sid or env_sid() or _only_sid(pid)
    cwd = str(Path(cwd).expanduser().resolve())
    if not sid and any(s["pid"] == pid and s.get("sid") for s in live()):
        # A process that runs several sessions by id: a caller that cannot say
        # which one gets no entry of its own, or it would be one more session
        # that peers could be routed to.
        return {"pid": pid, "cwd": cwd, "name": os.environ.get("TOXI_SESSION") or name
                or os.path.basename(cwd) or "session", "since": time.time(), "transient": True}
    _dir().mkdir(parents=True, exist_ok=True)
    with open(_dir() / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        mine = current(pid, sid)
        # A session by id is named by its adapter; a caller with no name for it
        # (the MCP server, a check) keeps that name.
        given = mine.get("base") if sid and mine and mine.get("cwd") == cwd else None
        base = (os.environ.get("TOXI_SESSION") or name or given or _claude_name(pid)
                or os.path.basename(cwd) or "session")
        base = re.sub(r"[^\w.-]+", "-", base).strip("-")[:60] or "session"
        taken = {n.lower() for s in live() if (s["pid"], s.get("sid")) != (pid, sid)
                 for n in [s["name"], *s.get("former", [])]}
        if mine and mine.get("cwd") == cwd and mine.get("base") == base \
                and (mine["name"].lower() == base.lower() or base.lower() in taken):
            if sid and time.time() - mine.get("seen", 0) > 60:  # renew, at most once a minute
                mine["seen"] = time.time()
                _file(pid, sid).write_text(json.dumps(mine))
            return mine
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            name = f"{base}-{n}"
        since = mine["since"] if mine else time.time()
        # Only a rename in place keeps the old names; a move is a new start.
        kept = mine.get("former", []) + [mine["name"]] if mine and mine["cwd"] == cwd else []
        former = [n for n in kept if n.lower() != name.lower()]
        mine = {"pid": pid, "cwd": cwd, "base": base, "name": name, "since": since}
        about = os.environ.get("TOXI_SESSION_ABOUT") or (mine or {}).get("about")
        if about:
            mine["about"] = about[:120]
        if sid:
            mine.update(sid=sid, seen=time.time())
        if former:
            mine["former"] = former
        _file(pid, sid).write_text(json.dumps(mine))
        return mine
