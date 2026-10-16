"""Design §4.6: sending files (and bodies over 64 KB as files), file metadata
that rides next to the transfer, and the receiver attaching it."""
import hashlib
import json
import selectors
from collections import deque
from pathlib import Path

import pytest
from click.testing import CliRunner

from toxi import db, envelope, paths
from toxi.cli import cli
from toxi.daemon import MAX_BODY_BYTES, Daemon, RpcError
from toxi.tox import FILE_CONTROL_CANCEL, FILE_KIND_DATA

from test_events import FakeTox, _events, _subscriber  # noqa: E402


class FileTox(FakeTox):
    def __init__(self):
        super().__init__()
        self.files, self.chunks, self.controls, self._next = [], [], [], 0

    def file_send(self, fn, kind, size, name):
        self.files.append((fn, kind, size, name))
        self._next += 1
        return self._next - 1

    def file_send_chunk(self, fn, num, pos, data):
        self.chunks.append((fn, num, pos, data))
        return True

    def file_control(self, fn, num, control):
        self.controls.append((fn, num, control))
        return True


def _daemon(tmp_path, monkeypatch):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FileTox()
    d.profile = {}
    d.ack_timeout, d.fail_after = 300, 86400
    d._text_times, d._request_times = {}, deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts, d._sel = [], {}, selectors.DefaultSelector()
    d._dropped_parts = deque(maxlen=200)
    d._transfers, d._out, d._file_meta = {}, {}, {}
    prof = json.dumps({"kind": "agent", "caps": list(envelope.CAPS)})
    d.db.execute("INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online, "
                 "profile_json) VALUES ('CC', 'bob', 1, 5, 1, ?)", (prof,))
    d.db.commit()
    return d


def _types(d):
    return [e["type"] for _, e in d.tox.sent]


def test_send_file_announces_meta_then_streams_chunks(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    f = tmp_path / "report.log"
    f.write_bytes(b"x" * 2500)
    b = _subscriber(d)
    res = d._send_file({"alias": "bob", "path": str(f), "thread": "ops", "kind": "report",
                        "session": "s1"})
    assert [e["data"]["from_session"] for _, e in d.tox.sent if e["type"] == "file"] == ["s1"]
    assert res["status"] == "sent" and res["msg_type"] == "file" and res["size"] == 2500
    assert res["sha256"] == hashlib.sha256(b"x" * 2500).hexdigest()
    (fn, meta), = [(fn, e) for fn, e in d.tox.sent if e["type"] == "file"]
    assert fn == 5 and meta["data"]["name"] == "report.log" and meta["data"]["size"] == 2500
    assert meta["data"]["msg_uuid"] == res["msg_uuid"] and meta["data"]["thread"] == "ops"
    assert meta["data"]["kind"] == "report" and meta["data"]["sha256"] == res["sha256"]
    assert d.tox.files == [(5, FILE_KIND_DATA, 2500, b"report.log")]
    # toxcore asks for chunks; the last request has length 0.
    d._on_file_chunk_request(5, 0, 0, 1000)
    d._on_file_chunk_request(5, 0, 1000, 1000)
    d._on_file_chunk_request(5, 0, 2000, 500)
    assert [(p, len(c)) for _, _, p, c in d.tox.chunks] == [(0, 1000), (1000, 1000), (2000, 500)]
    row = d.db.execute("SELECT status FROM messages").fetchone()
    assert row["status"] == "sent"
    d._on_file_chunk_request(5, 0, 2500, 0)
    row = d.db.execute("SELECT status, delivered_at, thread FROM messages").fetchone()
    assert row["status"] == "delivered" and row["delivered_at"] and row["thread"] == "ops"
    assert d._out == {}
    assert [e["event"] for e in _events(b)] == ["delivery"]
    with pytest.raises(RpcError) as e:
        d._send_file({"alias": "bob", "path": str(tmp_path / "nope")})
    assert e.value.code == "FILE_NOT_FOUND"


def test_offline_file_waits_and_starts_on_connect_and_restarts_on_drop(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d.tox.status = 0
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG" + b"0" * 100)
    res = d._send_file({"alias": "bob", "path": str(f)})
    assert res["status"] == "queued" and res["msg_type"] == "image" and d.tox.files == []
    d.tox.status = 2
    d._on_friend_connection_status(5, 2)  # flush starts the transfer
    assert d.tox.files and d.db.execute("SELECT status FROM messages").fetchone()["status"] == "sent"
    d._on_friend_connection_status(5, 0)  # link dropped mid-transfer
    assert d._out == {} and d.db.execute("SELECT status FROM messages").fetchone()["status"] == "queued"
    d.tox.files.clear()
    d._on_friend_connection_status(5, 2)
    assert len(d.tox.files) == 1  # offered again from scratch


def test_receiver_cancel_marks_failed_and_sweep_requeues_stale_sends(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    f = tmp_path / "a.bin"
    f.write_bytes(b"1" * 10)
    b = _subscriber(d)
    d._send_file({"alias": "bob", "path": str(f)})
    d._on_file_recv_control(5, 0, FILE_CONTROL_CANCEL)
    assert d.db.execute("SELECT status FROM messages").fetchone()["status"] == "failed"
    assert [e["event"] for e in _events(b)] == ["send.failed"]
    # A 'sent' file with no live transfer (e.g. after a daemon restart) is queued again.
    d.db.execute("UPDATE messages SET status='sent'")
    d.db.commit()
    d.tox.files.clear()
    d._retry_sweep()
    assert d.tox.files  # requeued, then flushed to the online friend


def test_body_over_limit_goes_as_a_text_file(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    big = "大" * (MAX_BODY_BYTES // 3 + 10)
    res = d._send_message({"alias": "bob", "body": big, "thread": "dump"})
    assert res["status"] == "sent" and res["msg_type"] == "file"
    p = Path(res["as_file"])
    assert p.exists() and p.read_text(encoding="utf-8") == big
    assert str(p).startswith(str(paths.media_dir() / "outgoing"))
    assert (p.stat().st_mode & 0o777) == 0o600
    assert d.tox.files[0][2] == p.stat().st_size and _types(d) == ["file"]


def _recv_file(d, friend, name, data, file_number=7):
    d._on_file_recv(friend, file_number, FILE_KIND_DATA, len(data), name.encode())
    d._on_file_recv_chunk(friend, file_number, 0, data)
    d._on_file_recv_chunk(friend, file_number, len(data), b"")


def test_receiver_attaches_meta_in_either_order(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    data = b"hello file"
    digest = hashlib.sha256(data).hexdigest()
    # meta first, then the transfer
    d._on_friend_message(5, envelope.encode(envelope.make_file_meta({
        "msg_uuid": "sender-1", "name": "notes.txt", "size": len(data), "sha256": digest,
        "thread": "ops", "kind": "report", "from_session": "ops-bot"})), 0)
    _recv_file(d, 5, "notes.txt", data)
    m = d._get_message({"msg_uuid": "sender-1"})["message"]
    assert m and m["msg_type"] == "file" and m["thread"] == "ops" and m["kind"] == "report"
    assert m["from_session"] == "ops-bot" and (Path(m["content"]).stat().st_mode & 0o777) == 0o600
    assert m["tags"] == [] and Path(m["content"]).read_bytes() == data
    assert d._file_meta == {} and len(d._hook_msgs) == 1 and d._hook_msgs[0]["msg_uuid"] == "sender-1"
    # transfer first, then meta (with a wrong digest -> flagged)
    _recv_file(d, 5, "late.txt", b"abc", file_number=8)
    d._on_friend_message(5, envelope.encode(envelope.make_file_meta({
        "msg_uuid": "sender-2", "name": "late.txt", "size": 3, "sha256": "0" * 64, "thread": "t2"})), 0)
    m = d._get_message({"msg_uuid": "sender-2"})["message"]
    assert m and m["thread"] == "t2" and m["tags"] == ["sha256-mismatch"]
    # a read receipt for it refers to the sender's id
    d.tox.sent.clear()
    d._mark_read({"msg_uuids": ["sender-2"]})
    assert [e["data"]["ref_uuid"] for _, e in d.tox.sent if e["type"] == "receipt"] == ["sender-2"]


def test_cli_and_mcp_send_file(monkeypatch, tmp_path):
    f = tmp_path / "shot.png"
    f.write_bytes(b"png")
    calls = []

    def request(method, params=None, **k):
        calls.append((method, params))
        return {"send_message": {"msg_uuid": "t", "status": "sent"},
                "send_file": {"msg_uuid": "f", "status": "queued", "name": "shot.png", "size": 3}}[method]

    monkeypatch.setattr("toxi.client.request", request)
    r = CliRunner().invoke(cli, ["send", "bob", "--file", str(f), "-t", "ui"])
    assert r.exit_code == 0, r.output
    assert calls[-1][0] == "send_file" and calls[-1][1]["path"] == str(f) and calls[-1][1]["thread"] == "ui"
    r = CliRunner().invoke(cli, ["send", "bob", "see attached", "--file", str(f)])
    assert [m for m, _ in calls[-2:]] == ["send_message", "send_file"] and "✓ file shot.png" in r.output
    both = json.loads(CliRunner().invoke(cli, ["--json", "send", "bob", "caption", "--file", str(f)]).output)
    assert both["text"]["msg_uuid"] == "t" and both["file"]["msg_uuid"] == "f"  # --json reports both
    assert CliRunner().invoke(cli, ["send", "bob"]).exit_code != 0

    from toxi import mcp_server
    monkeypatch.setattr(mcp_server.client, "request", request)
    monkeypatch.setenv("TOXI_SESSION", "mcp")
    mcp_server.send_file("bob", str(f), thread="ui", kind="report")
    assert calls[-1][1]["session"] == "mcp" and calls[-1][1]["kind"] == "report"
