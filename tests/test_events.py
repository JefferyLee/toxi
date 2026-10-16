"""P2 timeliness (design §4.3) on the daemon with a fake Tox handle: the event
stream, inbox.flag, multi-part reassembly, read receipts, live presence."""
import json
import selectors
import socket
import time
from collections import deque

import pytest

from toxi import db, envelope, ipc, paths
from toxi.daemon import MAX_BODY_BYTES, PART_TIMEOUT, TEXT_RATE_LIMIT, Daemon, RpcError, _merge_parts

ALL_CAPS = ("profile", "session", "receipt", "part")


class FakeTox:
    def __init__(self, status=2):
        self.sent = []  # (friend_number, parsed envelope)
        self.status = status

    def friend_send_message(self, fn, payload, msg_type=0):
        self.sent.append((fn, envelope.parse(payload)))
        return True

    def friend_get_connection_status(self, fn):
        return self.status

    def self_get_name(self):
        return b"me"

    def self_get_address_hex(self):
        return "A" * 76

    def get_savedata(self):
        return b"s"

    def is_connected(self):
        return True

    def self_get_connection_status(self):
        return 2


def _daemon(tmp_path, monkeypatch, caps=ALL_CAPS, status=2):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FakeTox(status)
    d.profile = {}
    d.ack_timeout, d.fail_after = 300, 86400
    d._started_at, d._last_archive = time.time(), 0.0
    d._text_times, d._request_times = {}, deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts, d._sel = [], {}, selectors.DefaultSelector()
    d._dropped_parts = deque(maxlen=200)
    d._transfers, d._out, d._file_meta = {}, {}, {}
    prof = json.dumps({"kind": "agent", "caps": list(caps)}) if caps is not None else None
    d.db.execute("INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online, "
                 "profile_json) VALUES ('CC', 'bob', 1, 5, 1, ?)", (prof,))
    d.db.commit()
    return d


def _subscriber(d, aliases=None, context=0):
    a, b = socket.socketpair()
    a.setblocking(False)
    d._subs.append({"sock": a, "aliases": set(aliases) if aliases else None, "context": context})
    return b


def _events(b):
    out = []
    b.settimeout(0.3)
    try:
        while True:
            out.append(ipc.recv_message(b))
    except (socket.timeout, ipc.IPCError, OSError):
        pass
    return out


def _types(d):
    return [e["type"] for _, e in d.tox.sent]


def _text(body, session=None, part=None):
    return envelope.encode(envelope.make_text(body, session, part))


# --- event stream -------------------------------------------------------------

def test_new_message_emits_event_and_touches_flag(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    b = _subscriber(d)
    assert not paths.inbox_flag_path().exists()
    d._on_friend_message(5, _text("hi", "s1"), 0)
    evs = _events(b)
    assert [e["event"] for e in evs] == ["message.new"]
    data = evs[0]["data"]
    assert data["alias"] == "bob" and data["content"] == "hi" and data["from_session"] == "s1"
    assert data["direction"] == "in" and data["profile"]["kind"] == "agent"
    assert paths.inbox_flag_path().exists()


def test_alias_filter_context_and_dead_subscriber(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    other = _subscriber(d, aliases=["carol"])
    ctx = _subscriber(d, context=2)
    dead = _subscriber(d)
    dead.close()
    d._on_friend_message(5, _text("one"), 0)
    d._on_friend_message(5, _text("two"), 0)
    assert _events(other) == []
    evs = _events(ctx)
    assert [m["content"] for m in evs[1]["data"]["context"]] == ["one", "two"]  # oldest first
    assert len(d._subs) == 2  # the closed one was dropped on the first write


def test_presence_delivery_and_failed_send_events(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    b = _subscriber(d)
    res = d._send_message({"alias": "bob", "body": "x"})
    d._on_friend_message(5, envelope.encode(envelope.make_ack(res["msg_uuid"])), 0)
    d._on_friend_connection_status(5, 0)
    evs = _events(b)
    assert [e["event"] for e in evs] == ["delivery", "contact.offline"]
    assert evs[0]["data"] == {"msg_uuid": res["msg_uuid"], "alias": "bob"}


# --- multi-part texts -----------------------------------------------------------

def _parts(pid, chunks, session=None):
    return [_text(c, session, {"id": pid, "i": i, "n": len(chunks)}) for i, c in enumerate(chunks, 1)]


def test_parts_reassembled_once_hook_and_event_once(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    b = _subscriber(d)
    p = _parts("pid-1", ["aa", "bb", "cc"], "sess")
    for raw in (p[2], p[0], p[1]):  # out of order
        d._on_friend_message(5, raw, 0)
    rows = d.db.execute("SELECT msg_uuid, content, from_session FROM messages").fetchall()
    assert [dict(r) for r in rows] == [{"msg_uuid": "pid-1", "content": "aabbcc", "from_session": "sess"}]
    assert _types(d).count("ack") == 3 and len(d._hook_msgs) == 1
    assert [e["event"] for e in _events(b)] == ["message.new"]
    # A late resend of a finished message is ACKed and otherwise ignored.
    d._on_friend_message(5, p[1], 0)
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 1
    assert _types(d).count("ack") == 4 and d._parts == {}


def test_incomplete_parts_are_stored_after_timeout(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    p = _parts("pid-2", ["aa", "bb", "cc"])
    d._on_friend_message(5, p[0], 0)
    d._on_friend_message(5, p[2], 0)
    d._tick_parts()
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0
    d._parts[(5, "pid-2")]["first"] -= PART_TIMEOUT + 1
    d._tick_parts()
    row = d.db.execute("SELECT content FROM messages").fetchone()
    assert row["content"] == "[toxi: only 2 of 3 parts arrived] aacc"


def test_parts_count_once_against_rate_limit_and_size_cap(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    for i in range(TEXT_RATE_LIMIT - 1):
        d._on_friend_message(5, _text(f"m{i}"), 0)
    for raw in _parts("pid-3", ["x", "y", "z"]):  # one logical message: still within the limit
        d._on_friend_message(5, raw, 0)
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == TEXT_RATE_LIMIT
    d._on_friend_message(5, _parts("pid-4", ["q", "r"])[0], 0)  # 61st: dropped, not ACKed
    assert (5, "pid-4") not in d._parts
    assert _types(d).count("ack") == TEXT_RATE_LIMIT - 1 + 3

    d2 = _daemon(tmp_path / "two", monkeypatch)
    big = ["x" * 1000] * (MAX_BODY_BYTES // 1000 + 2)
    for raw in _parts("pid-5", big):
        d2._on_friend_message(5, raw, 0)
    assert d2.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0
    assert d2._parts == {}


# --- read receipts --------------------------------------------------------------

def test_mark_read_sends_receipt_only_to_capable_online_peers(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_friend_message(5, _text("hi"), 0)
    uuid = d.db.execute("SELECT msg_uuid FROM messages").fetchone()["msg_uuid"]
    d.tox.sent.clear()
    assert d._mark_read({"msg_uuids": [uuid, uuid]}) == {"marked": 1}
    assert [(fn, e["type"], e["data"]["ref_uuid"]) for fn, e in d.tox.sent] == [(5, "receipt", uuid)]

    d2 = _daemon(tmp_path / "v1", monkeypatch, caps=("profile", "session"))
    d2._on_friend_message(5, _text("hi"), 0)
    d2.tox.sent.clear()
    d2._mark_read({"msg_uuids": [d2.db.execute("SELECT msg_uuid FROM messages").fetchone()[0]]})
    assert d2.tox.sent == []


def test_incoming_receipt_marks_outgoing_read_including_all_parts(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    b = _subscriber(d)
    res = d._send_message({"alias": "bob", "body": "y" * 3000})
    assert res["parts"] == 3 and all(e["data"]["part"]["id"] == res["msg_uuid"] for _, e in d.tox.sent)
    d._on_friend_message(5, envelope.encode(envelope.make_receipt(res["msg_uuid"])), 0)
    rows = d.db.execute("SELECT status, read_receipt_at, delivered_at FROM messages WHERE direction='out'").fetchall()
    assert len(rows) == 3 and all(r["status"] == "read" and r["read_receipt_at"] and r["delivered_at"] for r in rows)
    assert [e["event"] for e in _events(b)] == ["receipt"]
    merged = d._get_message({"msg_uuid": res["msg_uuid"]})["message"]
    assert merged["status"] == "read" and merged["content"] == "y" * 3000
    assert d._get_messages({"alias": "bob"})["messages"][0]["msg_uuid"] == res["msg_uuid"]


# --- sending: parts vs prefix, size cap, live presence ---------------------------

def test_send_uses_parts_for_capable_peers_and_prefix_for_others(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    res = d._send_message({"alias": "bob", "body": "z" * 2000, "session": "s"})
    bodies = [e["data"]["body"] for _, e in d.tox.sent]
    assert not any(b.startswith("[1/") for b in bodies) and "".join(bodies) == "z" * 2000
    assert all(e["data"]["from_session"] == "s" for _, e in d.tox.sent)
    msgs = d._get_messages({"alias": "bob"})["messages"]
    assert len(msgs) == 1 and msgs[0]["msg_uuid"] == res["msg_uuid"] and "part_id" not in msgs[0]

    old = _daemon(tmp_path / "old", monkeypatch, caps=("profile", "session"))
    old._send_message({"alias": "bob", "body": "z" * 2000})
    bodies = [e["data"]["body"] for _, e in old.tox.sent]
    assert bodies[0].startswith("[1/2] ") and all("part" not in e["data"] for _, e in old.tox.sent)
    assert len(old._get_messages({"alias": "bob"})["messages"]) == 2


def test_presence_comes_from_toxcore_not_the_db(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch, status=0)  # DB says online, toxcore says no
    res = d._send_message({"alias": "bob", "body": "hi"})
    assert res["status"] == "queued" and d.tox.sent == []
    assert d.db.execute("SELECT is_online FROM contacts").fetchone()["is_online"] == 0


def test_retry_sweep_flushes_queued_rows_for_online_friends(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d.db.execute("INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, "
                 "created_at, status) VALUES ('q1', 1, 'out', 'text', 'late', ?, 'queued')",
                 (int(time.time()),))
    d.db.commit()
    d._retry_sweep()
    assert d.db.execute("SELECT status FROM messages WHERE msg_uuid='q1'").fetchone()["status"] == "sent"
    assert [e["data"]["body"] for _, e in d.tox.sent] == ["late"]


def test_merge_parts_keeps_order_and_least_status():
    rows = [
        {"msg_uuid": "c", "content": "c", "status": "read", "part_id": "P", "part_i": 3, "part_n": 3, "created_at": 3},
        {"msg_uuid": "b", "content": "b", "status": "sent", "part_id": "P", "part_i": 2, "part_n": 3, "created_at": 2},
        {"msg_uuid": "x", "content": "solo", "status": "read", "part_id": None, "part_i": None, "part_n": None, "created_at": 2},
        {"msg_uuid": "a", "content": "a", "status": "delivered", "part_id": "P", "part_i": 1, "part_n": 3, "created_at": 1},
    ]
    out = _merge_parts(rows)
    assert [m["msg_uuid"] for m in out] == ["P", "x"]
    assert out[0]["content"] == "abc" and out[0]["status"] == "sent" and out[0]["created_at"] == 1
    assert "part_id" not in out[0] and "part_id" not in out[1]


def test_get_messages_limit_counts_logical_messages(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    big = d._send_message({"alias": "bob", "body": "y" * 3000})      # 3 parts
    d._on_friend_message(5, _text("one"), 0)
    d._on_friend_message(5, _text("two"), 0)
    got = d._get_messages({"alias": "bob", "limit": 2})["messages"]
    assert [m["content"] for m in got] == ["two", "one"]              # parts did not eat the window
    got = d._get_messages({"alias": "bob", "limit": 3})["messages"]
    assert got[2]["msg_uuid"] == big["msg_uuid"] and got[2]["content"] == "y" * 3000
    assert d._get_messages({"alias": "bob", "limit": 1, "unread_only": True})["messages"][0]["content"] == "two"
    assert d._get_status({})["version"]
