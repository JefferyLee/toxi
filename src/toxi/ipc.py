"""Length-prefixed JSON framing for the daemon <-> CLI socket (see PRD §4.6.2).

Wire format: [4 bytes big-endian uint32 length][UTF-8 JSON payload].
"""
import json
import socket
import struct

MAX_FRAME = 10 * 1024 * 1024  # 10 MiB guard against a runaway/garbage length prefix


class IPCError(Exception):
    pass


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise IPCError("connection closed before frame was complete")
        buf += chunk
    return bytes(buf)


def frame(obj) -> bytes:
    payload = json.dumps(obj).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise IPCError(f"frame too large: {len(payload)} bytes")
    return struct.pack(">I", len(payload)) + payload


def send_message(sock: socket.socket, obj) -> None:
    sock.sendall(frame(obj))


def recv_message(sock: socket.socket):
    (length,) = struct.unpack(">I", _recv_exactly(sock, 4))
    if length > MAX_FRAME:
        raise IPCError(f"frame too large: {length} bytes")
    return json.loads(_recv_exactly(sock, length).decode("utf-8"))
