"""End-to-end, two real daemons on this machine, bootstrapped to each other
(`[tox] bootstrap`) so they need neither LAN discovery nor NAT hairpinning:
pairing, cards, parts, receipts (read + done), reply routing, rooms, files.

Marked `dht`: the onion announce still needs the public DHT. Run with
`pytest --run-dht tests/test_e2e_local.py` (about a minute).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from toxi import client, invite

pytestmark = pytest.mark.dht
CONNECT_TIMEOUT = 150


def _sock(home):
    return Path(home) / "daemon.sock"


def _req(home, method, params=None):
    return client.request(method, params or {}, sock_path=_sock(home))


def _start(home: str, extra_config: str = "") -> subprocess.Popen:
    Path(home, "config.toml").write_text(extra_config)
    env = dict(os.environ, TOXI_HOME=home)
    p = subprocess.Popen([sys.executable, "-m", "toxi.daemon"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        if p.poll() is not None:
            raise RuntimeError("daemon exited during startup")
        try:
            _req(home, "get_status")
            return p
        except client.DaemonNotRunning:
            time.sleep(0.05)
    raise TimeoutError("daemon did not become ready")


def _stop(home, p):
    try:
        _req(home, "shutdown")
    except Exception:
        pass
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()


def _until(cond, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def _contact(home, alias):
    return next((c for c in _req(home, "list_contacts")["contacts"] if c["alias"] == alias), None)


@pytest.fixture
def pair():
    a = tempfile.mkdtemp(prefix="toxi-ea-", dir="/tmp")
    b = tempfile.mkdtemp(prefix="toxi-eb-", dir="/tmp")
    pa = _start(a)
    me = _req(a, "get_me")
    # B learns A's DHT node directly; both still use the public DHT for the onion.
    pb = _start(b, f'[tox]\nbootstrap = [{{ host = "127.0.0.1", port = {me["udp_port"]}, '
                   f'key = "{me["dht_id"]}" }}]\n')
    try:
        yield a, b
    finally:
        _stop(a, pa)
        _stop(b, pb)
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_two_daemons_end_to_end(pair):
    a, b = pair
    assert _req(a, "get_me")["dht_id"] and _req(a, "get_me")["udp_port"] > 0

    # --- cards + pairing -------------------------------------------------------
    _req(a, "set_name", {"name": "alice"})
    _req(b, "set_name", {"name": "bob"})
    _req(a, "set_profile", {"profile": {"kind": "agent", "runtime": "test", "operator": "pytest"}})
    _req(b, "set_profile", {"profile": {"kind": "agent", "runtime": "test", "operator": "pytest"}})
    inv = invite.decode(_req(a, "create_invite", {"ttl": 300})["invite"])
    _req(b, "add_contact", {"alias": "alice", "tox_id": inv["tox_id"],
                            "request_msg": invite.request_message(inv["nonce"], "bob")})
    t0 = time.time()
    _until(lambda: _contact(a, "bob"), CONNECT_TIMEOUT, "A to auto-accept B's invite reply")
    _until(lambda: (_contact(a, "bob") or {}).get("is_online") and (_contact(b, "alice") or {}).get("is_online"),
           CONNECT_TIMEOUT, "both online")
    connect_s = time.time() - t0
    _until(lambda: "part" in ((_contact(a, "bob") or {}).get("profile") or {}).get("caps", []),
           30, "B's card on A")
    assert "receipt" in _contact(b, "alice")["profile"]["caps"]

    # --- parts: one logical message each side -----------------------------------
    body = "分段 part " * 2500  # ~27 KB
    res = _req(a, "send_message", {"alias": "bob", "body": body, "thread": "t1",
                                   "session_dir": "/w/asker", "kind": "request"})
    assert res["parts"] > 1
    got = _until(lambda: [m for m in _req(b, "get_messages", {"alias": "alice"})["messages"]
                          if m["msg_uuid"] == res["msg_uuid"]], 60, "B to reassemble")
    assert got[0]["content"] == body and got[0]["thread"] == "t1" and got[0]["kind"] == "request"
    _until(lambda: _req(a, "get_message", {"msg_uuid": res["msg_uuid"]})["message"]["status"] == "delivered",
           30, "all parts acked")

    # --- read receipt, then a reply that routes back and marks it handled ---------
    _req(b, "mark_read", {"msg_uuids": [res["msg_uuid"]]})
    _until(lambda: _req(a, "get_message", {"msg_uuid": res["msg_uuid"]})["message"]["status"] == "read",
           30, "read receipt")
    rep = _req(b, "send_message", {"alias": "alice", "body": "done it", "thread": "t1",
                                   "reply_to": res["msg_uuid"], "kind": "report"})
    _until(lambda: _req(a, "get_message", {"msg_uuid": res["msg_uuid"]})["message"]["status"] == "handled",
           30, "done receipt")
    reply = _until(lambda: _req(a, "get_message", {"msg_uuid": rep["msg_uuid"]})["message"], 30, "reply on A")
    assert reply["route_dir"] == "/w/asker" and reply["reply_to"] == res["msg_uuid"]
    threads = {t["thread"]: t for t in _req(a, "list_threads", {})["threads"]}
    assert threads["t1"]["open_requests"] == 0

    # --- room ------------------------------------------------------------------
    room = _req(a, "create_room", {"name": "ops", "aliases": ["bob"]})
    _until(lambda: [r for r in _req(b, "list_rooms", {})["rooms"] if r["room_id"] == room["room_id"]],
           30, "room card on B")
    _req(a, "send_room", {"room": "ops", "body": "standup?", "thread": "daily"})
    _until(lambda: [m for m in _req(b, "get_messages", {"room": "ops"})["messages"] if m["content"] == "standup?"],
           30, "room message on B")
    _req(b, "send_room", {"room": "ops", "body": "here"})
    _until(lambda: [m for m in _req(a, "get_messages", {"room": "ops"})["messages"] if m["content"] == "here"],
           30, "room reply on A")

    # --- file --------------------------------------------------------------------
    f = Path(a) / "report.log"
    f.write_bytes(os.urandom(50_000))
    sent = _req(a, "send_file", {"alias": "bob", "path": str(f), "thread": "files"})
    got = _until(lambda: _req(b, "get_message", {"msg_uuid": sent["msg_uuid"]})["message"], 60, "file on B")
    assert got["msg_type"] == "file" and got["thread"] == "files" and "sha256-mismatch" not in got["tags"]
    assert hashlib.sha256(Path(got["content"]).read_bytes()).hexdigest() == sent["sha256"]
    _until(lambda: _req(a, "get_message", {"msg_uuid": sent["msg_uuid"]})["message"]["status"] == "delivered",
           30, "file delivered")
    print(f"\nconnected in {connect_s:.0f}s")
