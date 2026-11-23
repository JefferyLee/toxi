"""Wake an idle Codex session when peer mail for it arrives (design §13.10).

Codex runs the threads of its sessions in a shared app-server daemon, whose
control socket speaks JSON-RPC over WebSocket. When mail routed to a session
should wake it (`[wake] kinds`) and its thread is idle, the waker appends the
mail to the thread as a developer message and starts a turn with no user input:
the peer's words never pass for the user's. A running thread gets its mail from
the hooks, as before.

One waker runs per toxi identity and Codex home; the Codex SessionStart and
Stop hooks start it (`toxi codex-wake`), and a second one exits at once. It
only touches threads that a hook or the MCP server registered for this
identity. It renews their entries while the daemon has them loaded, and ends
them when it lets them go.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import select
import socket
import struct
import time
from pathlib import Path

from . import __version__, client, paths, peers, sessions

POLL = 1.0  # a stat of inbox.flag a second
RENEW = 600  # well inside sessions.SID_TTL
WAKE_LIMIT = 20  # wakes per thread per hour: two agents must not wake each other for ever
IDLE_EXIT = 3600  # no session of this identity in the daemon for this long: stop


def socket_path(codex_home: str | Path) -> Path:
    return Path(codex_home) / "app-server-control" / "app-server-control.sock"


class Daemon:
    """A JSON-RPC client of the Codex app-server daemon: WebSocket on a unix socket."""

    def __init__(self, path: Path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(10)
        self.s.connect(os.path.realpath(path))  # the real path is short enough for AF_UNIX
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise EOFError("closed during the handshake")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("not a WebSocket server")
        self.nid = 0
        self.call("initialize", {"clientInfo": {"name": "toxi", "version": __version__},
                                 "capabilities": {"experimentalApi": True}})
        self._send({"jsonrpc": "2.0", "method": "initialized"})

    def _frame(self, op: int, data: bytes) -> None:
        n = len(data)
        if n < 126:
            head = bytes([0x80 | op, 0x80 | n])
        elif n < 65536:
            head = bytes([0x80 | op, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x80 | op, 0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _send(self, msg: dict) -> None:
        self._frame(0x1, json.dumps(msg).encode())

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError("the daemon closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv(self) -> dict | None:
        """The next JSON message; None for a control frame."""
        parts = []
        while True:
            b0, b1 = self._exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            if b1 & 0x80:
                mask = self._exact(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._exact(n)))
            else:
                data = self._exact(n)
            op = b0 & 0x0F
            if op == 0x8:
                raise EOFError("the daemon closed the connection")
            if op == 0x9:
                self._frame(0xA, data)
                return None
            if op == 0xA:
                return None
            parts.append(data)
            if b0 & 0x80:
                try:
                    return json.loads(b"".join(parts))
                except ValueError:
                    return None

    def call(self, method: str, params: dict) -> dict:
        self.nid += 1
        rid = self.nid
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            m = self._recv()
            if m and m.get("id") == rid and "method" not in m:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result") or {}

    def drain(self, timeout: float) -> None:
        """Wait up to `timeout`, reading what the daemon sends (answering pings)."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if not self.buf and (left <= 0 or not select.select([self.s], [], [], left)[0]):
                return
            self._recv()


def check(d: Daemon, pid: int, renew: bool, woken: dict) -> int:
    """Look at the threads of this identity's sessions in the daemon. Wake each
    idle one whose mail should wake it. Returns the number of those sessions."""
    ours = {s["sid"]: s for s in sessions.live() if s["pid"] == pid and s.get("sid")}
    if not ours:
        return 0
    loaded = set(d.call("thread/loaded/list", {}).get("data") or [])
    now = time.time()
    for sid, s in ours.items():
        if sid not in loaded:
            sessions.end(pid, sid)  # the daemon let the thread go: the session ended
            continue
        thread = d.call("thread/read", {"threadId": sid}).get("thread") or {}
        cwd = thread.get("cwd") or s["cwd"]
        if renew:
            sessions.register(cwd, pid=pid, sid=sid)
        if (thread.get("status") or {}).get("type") != "idle":
            continue  # it runs: the hooks deliver
        p = peers.peek(None, cwd, sid=sid)
        if not p["text"] or not p["wake"]:
            continue  # nothing, or nothing that wakes: the next run gets it
        recent = [t for t in woken.get(sid, []) if now - t < 3600]
        if len(recent) >= WAKE_LIMIT:
            continue
        item = {"type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": p["text"]}]}
        try:
            d.call("thread/inject_items", {"threadId": sid, "items": [item]})
            d.call("turn/start", {"threadId": sid, "input": []})
        except RuntimeError:
            continue  # it started a run meanwhile: the mail stays unread for the hooks
        woken[sid] = recent + [now]
        if p["msg_uuids"]:
            peers.mark_read(p["msg_uuids"], sessions.current(pid, sid))
    return len(ours)


def run(codex_home: str, agent_pid: int) -> None:
    """Watch for mail and wake idle Codex sessions, until the daemon goes or no
    session of this identity has been in it for an hour."""
    path = socket_path(codex_home)
    tag = hashlib.sha1(str(Path(codex_home).expanduser().resolve()).encode()).hexdigest()[:12]
    lock = open(paths.config_dir() / f"codex-wake-{tag}.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # one runs already
    os.environ["TOXI_AGENT_PID"] = str(agent_pid)  # the daemon: the process of these sessions
    try:
        d = Daemon(path)
    except (OSError, EOFError, ConnectionError, RuntimeError):
        return
    flag = paths.config_dir() / "inbox.flag"
    last_flag, last_renew, last_ours, woken = None, 0.0, time.time(), {}
    while True:
        try:
            d.drain(POLL)
        except (OSError, EOFError):
            return
        try:
            mtime = flag.stat().st_mtime
        except OSError:
            mtime = None
        now = time.time()
        renew = now - last_renew >= RENEW
        if mtime == last_flag and not renew:
            continue
        last_flag = mtime
        if renew:
            last_renew = now
        try:
            ours = check(d, agent_pid, renew, woken)
        except (OSError, EOFError):
            return
        except (RuntimeError, client.DaemonError):
            continue  # toxi's daemon restarts, or one call failed: the next look tries again
        if ours:
            last_ours = now
        elif now - last_ours > IDLE_EXIT:
            return
