"""Daemon skeleton integration tests.

These run a real daemon (in a thread, with a temp config dir) and talk to it
over the IPC socket. They do not require DHT connectivity, but they construct
real libtoxcore handles and are skipped unless pytest is run with --run-toxcore.
"""
import os
import shutil
import tempfile
import threading
import time

import pytest

from toxi import client, db, envelope
from toxi.daemon import Daemon
from toxi.tox import Tox


pytestmark = pytest.mark.toxcore


def _foreign_tox_id() -> str:
    """A valid foreign Tox ID (correct checksum) to use as an add target."""
    t = Tox()
    try:
        return t.self_get_address_hex()
    finally:
        t.kill()


@pytest.fixture
def home(monkeypatch):
    # Short path under /tmp: macOS caps AF_UNIX socket paths at ~104 chars,
    # and pytest's tmp_path is too deep once "/daemon.sock" is appended.
    d = tempfile.mkdtemp(prefix="toxi-", dir="/tmp")
    monkeypatch.setenv("TOXI_HOME", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _start_daemon():
    errors: list[Exception] = []

    def target():
        try:
            Daemon().run()
        except Exception as e:  # surfaced via the readiness poll below
            errors.append(e)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        if errors:
            raise errors[0]
        try:
            client.request("get_status")
            return t
        except client.DaemonNotRunning:
            time.sleep(0.05)
    raise TimeoutError("daemon did not become ready")


def _stop_daemon(t):
    try:
        client.request("shutdown")
    except Exception:
        pass
    t.join(timeout=5)


def test_mark_read_counts_only_unread_incoming_messages(tmp_path):
    conn = db.connect(tmp_path / "chat.db")
    d = object.__new__(Daemon)
    d.db = conn
    now = int(time.time())
    try:
        conn.execute(
            "INSERT INTO contacts (public_key, alias, added_at) VALUES (?, ?, ?)",
            ("AB" * 32, "bob", now),
        )
        cid = conn.execute("SELECT id FROM contacts WHERE alias='bob'").fetchone()["id"]
        conn.executemany(
            "INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, "
            "created_at, status, read_at) VALUES (?, ?, ?, 'text', ?, ?, ?, ?)",
            [
                ("in-unread", cid, "in", "hi", now, "received", None),
                ("in-read", cid, "in", "seen", now, "read", now),
                ("out", cid, "out", "sent", now, "sent", None),
            ],
        )
        conn.commit()

        res = d._mark_read({"msg_uuids": ["in-unread", "in-read", "out", "missing"]})

        assert res == {"marked": 1}
        row = conn.execute(
            "SELECT status, read_at FROM messages WHERE msg_uuid='in-unread'"
        ).fetchone()
        assert row["status"] == "read"
        assert row["read_at"] is not None
    finally:
        conn.close()


def test_get_me_set_name_status(home):
    t = _start_daemon()
    try:
        me = client.request("get_me")
        assert len(me["tox_id"]) == 76  # 38-byte address as hex
        assert me["name"] == ""

        assert client.request("set_name", {"name": "alice"})["name"] == "alice"
        assert client.request("get_me")["name"] == "alice"

        st = client.request("get_status")
        assert {"pid", "uptime_seconds", "dht_connected", "tox_id", "contacts",
                "contacts_total", "contacts_online", "queue_size", "stats_24h"} <= set(st)
        assert st["queue_size"] == 0
        assert st["contacts_total"] == 0 and st["contacts_online"] == 0
        assert st["stats_24h"] == {"sent": 0, "received": 0, "failed": 0}
    finally:
        _stop_daemon(t)


def test_identity_and_name_persist_across_restart(home):
    t = _start_daemon()
    me1 = client.request("get_me")
    client.request("set_name", {"name": "alice"})
    _stop_daemon(t)

    t2 = _start_daemon()
    try:
        me2 = client.request("get_me")
        assert me2["tox_id"] == me1["tox_id"]  # same identity from tox_state.bin
        assert me2["name"] == "alice"          # name persisted too
    finally:
        _stop_daemon(t2)


def test_add_and_list_contacts(home):
    foreign = _foreign_tox_id()
    t = _start_daemon()
    try:
        assert client.request("add_contact", {"alias": "bob", "tox_id": foreign})["alias"] == "bob"
        contacts = client.request("list_contacts")["contacts"]
        assert len(contacts) == 1
        assert contacts[0]["alias"] == "bob"
        assert contacts[0]["tox_id"] == foreign
        assert contacts[0]["public_key"] == foreign[:64]
        assert contacts[0]["is_online"] == 0
    finally:
        _stop_daemon(t)


def test_contacts_persist_across_restart(home):
    foreign = _foreign_tox_id()
    t = _start_daemon()
    client.request("add_contact", {"alias": "bob", "tox_id": foreign})
    _stop_daemon(t)

    t2 = _start_daemon()
    try:
        contacts = client.request("list_contacts")["contacts"]
        assert len(contacts) == 1 and contacts[0]["alias"] == "bob"
    finally:
        _stop_daemon(t2)


def test_add_rejects_bad_tox_id(home):
    t = _start_daemon()
    try:
        with pytest.raises(client.DaemonError) as ei:
            client.request("add_contact", {"alias": "x", "tox_id": "deadbeef"})
        assert ei.value.code == "INVALID_TOX_ID"
    finally:
        _stop_daemon(t)


def test_add_rejects_duplicate_alias(home):
    id1, id2 = _foreign_tox_id(), _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": id1})
        with pytest.raises(client.DaemonError) as ei:
            client.request("add_contact", {"alias": "bob", "tox_id": id2})
        assert ei.value.code == "CONTACT_EXISTS"
    finally:
        _stop_daemon(t)


def test_remove_contact_clears_history_and_frees_alias(home):
    foreign = _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": foreign})
        client.request("send_message", {"alias": "bob", "body": "hi"})

        client.request("remove_contact", {"alias": "bob"})
        assert client.request("list_contacts")["contacts"] == []
        assert client.request("list_queue")["queue"] == []

        # Alias is free again; re-adding the same Tox ID must succeed.
        client.request("add_contact", {"alias": "bob", "tox_id": foreign})
        assert len(client.request("list_contacts")["contacts"]) == 1
    finally:
        _stop_daemon(t)


def test_remove_unknown_contact_errors(home):
    t = _start_daemon()
    try:
        with pytest.raises(client.DaemonError) as ei:
            client.request("remove_contact", {"alias": "ghost"})
        assert ei.value.code == "CONTACT_NOT_FOUND"
    finally:
        _stop_daemon(t)


def test_list_requests_empty(home):
    t = _start_daemon()
    try:
        assert client.request("list_requests")["requests"] == []
    finally:
        _stop_daemon(t)


def test_send_to_offline_contact_queues(home):
    foreign = _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": foreign})
        res = client.request("send_message", {"alias": "bob", "body": "hi bob"})
        assert res["status"] == "queued"

        q = client.request("list_queue")["queue"]
        assert len(q) == 1 and q[0]["alias"] == "bob" and q[0]["content"] == "hi bob"

        msgs = client.request("get_messages", {"alias": "bob"})["messages"]
        assert len(msgs) == 1
        assert msgs[0]["direction"] == "out" and msgs[0]["status"] == "queued"
    finally:
        _stop_daemon(t)


def test_status_reflects_contacts_and_queue(home):
    foreign = _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": foreign})
        client.request("send_message", {"alias": "bob", "body": "hi"})  # offline -> queued
        st = client.request("get_status")
        assert st["contacts_total"] == 1
        assert st["contacts"][0]["alias"] == "bob"
        assert st["queue_size"] == 1
    finally:
        _stop_daemon(t)


def test_send_to_unknown_contact_errors(home):
    t = _start_daemon()
    try:
        with pytest.raises(client.DaemonError) as ei:
            client.request("send_message", {"alias": "nobody", "body": "hi"})
        assert ei.value.code == "CONTACT_NOT_FOUND"
    finally:
        _stop_daemon(t)


def test_send_too_long_message_is_split_into_parts(home):
    foreign = _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": foreign})
        res = client.request("send_message", {"alias": "bob", "body": "x" * 1400})
        assert res["status"] == "queued" and res["parts"] == 2  # bob is offline
        assert len(client.request("list_queue", {})["queue"]) == 2
    finally:
        _stop_daemon(t)


def test_retry_sweep_marks_failed(home):
    """A 'sent' message left unacked past fail_after becomes 'failed'."""
    with open(os.path.join(home, "config.toml"), "w") as f:
        f.write("[retry]\nack_timeout_minutes = 0.001\nfail_after_hours = 0.001\n")
    d = Daemon()  # constructed in this thread, so its db is ours to query
    try:
        now = int(time.time())
        d.db.execute(
            "INSERT INTO contacts (public_key, alias, added_at, is_online, friend_number) "
            "VALUES (?, ?, ?, 0, 0)",
            ("AB" * 32, "bob", now),
        )
        cid = d.db.execute("SELECT id FROM contacts WHERE alias='bob'").fetchone()["id"]
        d.db.execute(
            "INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, "
            "created_at, status, last_attempt_at) VALUES (?, ?, 'out', 'text', ?, ?, 'sent', ?)",
            ("u1", cid, "old msg", now - 100, now - 100),
        )
        d.db.commit()

        d._retry_sweep()

        status = d.db.execute("SELECT status FROM messages WHERE msg_uuid='u1'").fetchone()["status"]
        assert status == "failed"
    finally:
        d.tox.kill()
        d.db.close()


def test_receive_and_accept_introduction(home):
    """contact_share -> pending introduction -> accept adds the contact."""
    carol_id = _foreign_tox_id()
    d = Daemon()
    try:
        now = int(time.time())
        d.db.execute(
            "INSERT INTO contacts (public_key, alias, added_at, is_online, friend_number) "
            "VALUES (?, 'alice', ?, 1, 7)",
            ("CD" * 32, now),
        )
        d.db.commit()

        share = envelope.encode(envelope.make_contact_share(carol_id, "carol", "my coworker"))
        d._on_friend_message(7, share, 0)

        intros = d._list_introductions({})["introductions"]
        assert len(intros) == 1
        assert intros[0]["from_alias"] == "alice"
        assert intros[0]["suggested_alias"] == "carol"
        assert intros[0]["introduced_tox_id"] == carol_id

        res = d._accept_introduction({"from_alias": "alice", "whom": "carol", "alias": None})
        assert res["alias"] == "carol"
        c = d.db.execute("SELECT tox_id, added_by FROM contacts WHERE alias='carol'").fetchone()
        assert c["tox_id"] == carol_id
        assert c["added_by"] == "introduce:alice"
        assert d._list_introductions({})["introductions"] == []  # no longer pending
    finally:
        d.tox.kill()
        d.db.close()


def test_introduce_offline_recipient_errors(home):
    bob_id, carol_id = _foreign_tox_id(), _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": bob_id})
        client.request("add_contact", {"alias": "carol", "tox_id": carol_id})
        with pytest.raises(client.DaemonError) as ei:
            client.request("introduce", {"to_alias": "bob", "contact_alias": "carol"})
        assert ei.value.code == "RECIPIENT_OFFLINE"
    finally:
        _stop_daemon(t)


def test_accept_intro_not_found_errors(home):
    bob_id = _foreign_tox_id()
    t = _start_daemon()
    try:
        client.request("add_contact", {"alias": "bob", "tox_id": bob_id})
        with pytest.raises(client.DaemonError) as ei:
            client.request("accept_introduction", {"from_alias": "bob", "whom": "ghost"})
        assert ei.value.code == "INTRO_NOT_FOUND"
    finally:
        _stop_daemon(t)


def test_log_handler_rotates(home):
    """The daemon logs through a RotatingFileHandler (PRD §4.11: 10MB x 5)."""
    import toxi.daemon as dmod
    from logging.handlers import RotatingFileHandler

    d = Daemon()
    try:
        rotating = [h for h in dmod.log.handlers if isinstance(h, RotatingFileHandler)]
        assert rotating, "no RotatingFileHandler configured"
        assert rotating[0].maxBytes == 10 * 1024 * 1024
        assert rotating[0].backupCount == 5
    finally:
        d.tox.kill()
        d.db.close()


def test_unknown_method_errors(home):
    t = _start_daemon()
    try:
        with pytest.raises(client.DaemonError) as ei:
            client.request("nonexistent")
        assert ei.value.code == "METHOD_NOT_FOUND"
    finally:
        _stop_daemon(t)
