"""Long messages: UTF-8 envelope encoding and automatic splitting into parts."""
import re

from toxi import db, envelope
from toxi.daemon import Daemon
from toxi.tox import MAX_MESSAGE_LENGTH

LIMIT = MAX_MESSAGE_LENGTH


def _unsplit(parts):
    return "".join(re.sub(r"^\[\d+/\d+\] ", "", p) for p in parts)


def test_short_body_unchanged():
    assert envelope.split_text("hi", LIMIT) == ["hi"]


def test_cjk_encoded_as_utf8_not_escaped():
    raw = envelope.encode(envelope.make_text("你好"))
    assert "你好".encode("utf-8") in raw and b"\\u" not in raw


def test_long_cjk_and_escapes_split_within_limit_and_reassemble():
    body = ("协议定稿后写进文档。" * 120) + ' "quotes" \\ and\nnewlines\t' * 30
    parts = envelope.split_text(body, LIMIT)
    assert len(parts) > 1
    assert all(len(envelope.encode(envelope.make_text(p))) <= LIMIT for p in parts)
    assert parts[0].startswith(f"[1/{len(parts)}] ")
    assert _unsplit(parts) == body


class FakeTox:
    def __init__(self, fail_from=None):
        self.sent, self.fail_from = [], fail_from

    def friend_get_connection_status(self, friend_number):
        return 2

    def friend_send_message(self, friend_number, payload):
        if self.fail_from is not None and len(self.sent) >= self.fail_from:
            return False
        self.sent.append(envelope.parse(payload)["data"]["body"])
        return True


def _daemon(tmp_path, monkeypatch, tox):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "chat.db")
    conn.execute("INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online) "
                 "VALUES ('AA', 'bob', 1, 5, 1)")
    conn.commit()
    d = object.__new__(Daemon)
    d.db, d.tox = conn, tox
    d._subs, d._parts = [], {}
    return d


def _rows(d):
    return d.db.execute("SELECT content, status FROM messages ORDER BY id").fetchall()


def test_send_message_splits_and_sends_parts_in_order(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch, FakeTox())
    body = "长消息" * 400
    res = d._send_message({"alias": "bob", "body": body})
    assert res["status"] == "sent" and res["parts"] == len(res["msg_uuids"]) > 1
    assert _unsplit(d.tox.sent) == body
    assert [r["status"] for r in _rows(d)] == ["sent"] * res["parts"]


def test_failed_part_queues_the_rest_to_keep_order(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch, FakeTox(fail_from=1))
    res = d._send_message({"alias": "bob", "body": "x" * 4000})
    assert res["status"] == "queued" and len(d.tox.sent) == 1
    statuses = [r["status"] for r in _rows(d)]
    assert statuses[0] == "sent" and set(statuses[1:]) == {"queued"}


def test_short_message_result_shape_unchanged(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch, FakeTox())
    assert set(d._send_message({"alias": "bob", "body": "hi"})) == {"msg_uuid", "status"}
