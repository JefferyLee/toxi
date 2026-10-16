"""Minimal IPC client: one request, one response, close (PRD §3.2).

Used by the CLI and by tests to talk to the daemon over its Unix socket.
"""
import asyncio
import socket
import time
from pathlib import Path

from . import ipc, paths


class DaemonNotRunning(Exception):
    pass


class DaemonError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def request(method: str, params: dict | None = None, *, sock_path: Path | None = None):
    sp = str(sock_path or paths.socket_path())
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    try:
        s.connect(sp)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonNotRunning("daemon is not running — start it with `toxi daemon start`") from e
    try:
        ipc.send_message(s, {"id": "1", "method": method, "params": params or {}})
        resp = ipc.recv_message(s)
    finally:
        s.close()

    if "error" in resp:
        err = resp["error"]
        raise DaemonError(err.get("code", "UNKNOWN"), err.get("message", ""))
    return resp["result"]


def _subscribe_params(aliases, context) -> dict:
    return {"aliases": list(aliases) if aliases else [], "context": int(context or 0)}


def subscribe(aliases=None, context: int = 0, *, sock_path: Path | None = None):
    """Yield daemon events ({"event", "data", "ts"}) until the connection drops.

    `aliases` limits events to those contacts; `context` > 0 attaches the last N
    messages with the sender to each message.new event (for memoryless agents).
    Raises DaemonError(METHOD_NOT_FOUND) against a daemon too old to stream.
    """
    sp = str(sock_path or paths.socket_path())
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    try:
        s.connect(sp)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonNotRunning("daemon is not running — start it with `toxi daemon start`") from e
    try:
        ipc.send_message(s, {"id": "1", "method": "subscribe",
                             "params": _subscribe_params(aliases, context)})
        resp = ipc.recv_message(s)
        if "error" in resp:
            err = resp["error"]
            raise DaemonError(err.get("code", "UNKNOWN"), err.get("message", ""))
        s.settimeout(None)
        while True:
            yield ipc.recv_message(s)
    finally:
        s.close()


async def asubscribe(aliases=None, context: int = 0, *, deadline: float | None = None,
                     sock_path: Path | None = None):
    """Async form of subscribe(); stops yielding when `deadline` (monotonic) passes."""
    sp = str(sock_path or paths.socket_path())
    try:
        reader, writer = await asyncio.open_unix_connection(sp)
    except OSError as e:  # missing socket, refused, or an unusable path
        raise DaemonNotRunning("daemon is not running — start it with `toxi daemon start`") from e

    async def read_frame():
        (length,) = __import__("struct").unpack(">I", await reader.readexactly(4))
        return __import__("json").loads((await reader.readexactly(length)).decode("utf-8"))

    try:
        writer.write(ipc.frame({"id": "1", "method": "subscribe",
                                "params": _subscribe_params(aliases, context)}))
        await writer.drain()
        resp = await asyncio.wait_for(read_frame(), 5.0)
        if "error" in resp:
            err = resp["error"]
            raise DaemonError(err.get("code", "UNKNOWN"), err.get("message", ""))
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return
            try:
                yield await asyncio.wait_for(read_frame(), remaining)
            except asyncio.TimeoutError:
                return
    except asyncio.IncompleteReadError:
        return
    finally:
        writer.close()
