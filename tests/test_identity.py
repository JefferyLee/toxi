"""Identity & security (design §4.1–4.2) on the daemon, with a fake Tox handle:
invite auto-accept, profile cards, session labels, rate limits, file modes."""
import json
import os
import stat
import time

from toxi import db, envelope, invite, paths
from toxi.daemon import Daemon, _harden_permissions


class FakeTox:
    def __init__(self):
        self.sent = []  # (friend_number, payload bytes)
        self.added = []
        self.name = b"ada"

    def friend_add_norequest(self, pk):
        self.added.append(pk.hex().upper())
        return 10 + len(self.added)

    def friend_send_message(self, fn, payload, msg_type=0):
        self.sent.append((fn, payload))
        return True

    def friend_get_connection_status(self, friend_number):
        return 2

    def self_get_name(self):
        return self.name

    def self_get_address_hex(self):
        return "A" * 76

    def get_savedata(self):
        return b"state"


def _daemon(tmp_path, monkeypatch, contact=True):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FakeTox()
    d.profile = {}
    d._text_times, d._request_times = {}, __import__("collections").deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts = [], {}
    if contact:
        d.db.execute("INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online) "
                     "VALUES ('CC', 'bob', 1, 5, 1)")
        d.db.commit()
    return d


def _types(d):
    return [envelope.parse(p)["type"] for _, p in d.tox.sent]


# --- invites ---------------------------------------------------------------

def test_invite_reply_is_auto_accepted_and_named(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    res = d._create_invite({"ttl": 60})
    data = invite.decode(res["invite"])
    assert data["tox_id"] == "A" * 76 and data["name"] == "ada"

    pk = bytes.fromhex("BB" * 32)
    d._on_friend_request(pk, invite.request_message(data["nonce"], "codex@Ada").encode())

    row = d.db.execute("SELECT alias, added_by, friend_number FROM contacts "
                       "WHERE public_key=?", ("BB" * 32,)).fetchone()
    assert dict(row) == {"alias": "codex@Ada", "added_by": "invite", "friend_number": 11}
    assert d.tox.added == ["BB" * 32]
    assert d.db.execute("SELECT COUNT(*) c FROM friend_requests").fetchone()["c"] == 0
    assert d.db.execute("SELECT used_at FROM invites").fetchone()["used_at"]
    assert paths.tox_state_path().exists()  # state saved after the friend list changed

    # The nonce is single use: a second reply lands in the ordinary pending list.
    d._on_friend_request(bytes.fromhex("DD" * 32),
                         invite.request_message(data["nonce"], "mallory").encode())
    pending = d.db.execute("SELECT public_key FROM friend_requests WHERE status='pending'").fetchall()
    assert [r["public_key"] for r in pending] == ["DD" * 32]


def test_expired_or_unknown_nonce_is_not_accepted(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d.db.execute("INSERT INTO invites (nonce, created_at, expires_at) VALUES ('old', 1, 2)")
    d.db.commit()
    d._on_friend_request(bytes.fromhex("EE" * 32), invite.request_message("old", "x").encode())
    d._on_friend_request(bytes.fromhex("EF" * 32), invite.request_message("nope", "y").encode())
    d._on_friend_request(bytes.fromhex("F0" * 32), b"plain old request")
    assert d.tox.added == []
    assert d.db.execute("SELECT COUNT(*) c FROM friend_requests").fetchone()["c"] == 3


def test_invited_alias_gets_suffix_on_collision(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)  # 'bob' already exists
    data = invite.decode(d._create_invite({})["invite"])
    d._on_friend_request(bytes.fromhex("BB" * 32), invite.request_message(data["nonce"], "bob").encode())
    row = d.db.execute("SELECT alias FROM contacts WHERE public_key=?", ("BB" * 32,)).fetchone()
    assert row["alias"] == "bob-2"


def test_friend_requests_are_rate_limited(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    for i in range(8):
        d._on_friend_request(bytes([i]) * 32, b"hi")
    assert d.db.execute("SELECT COUNT(*) c FROM friend_requests").fetchone()["c"] == 5


# --- profile cards ----------------------------------------------------------

def test_incoming_profile_is_stored_on_the_contact(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    card = {"kind": "agent", "runtime": "grok", "host": "vm1", "operator": "jeff", "proto": 2}
    d._on_friend_message(5, envelope.encode(envelope.make_profile(card)), 0)
    c = d._list_contacts({})["contacts"][0]
    assert c["profile"] == card and c["proto"] == 2
    assert d.tox.sent == []  # a profile is never ACKed or answered


def test_set_profile_persists_broadcasts_and_is_sent_on_connect(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    res = d._set_profile({"profile": {"kind": "agent", "runtime": "claude-code", "bogus": 1}})
    prof = res["profile"]
    assert prof["kind"] == "agent" and prof["name"] == "ada" and "bogus" not in prof
    assert prof["proto"] == envelope.VERSION and prof["caps"] == envelope.CAPS
    assert res["sent_to"] == 1 and _types(d) == ["profile"]
    assert stat.S_IMODE(os.stat(paths.profile_path()).st_mode) == 0o600
    assert json.loads(paths.profile_path().read_text()) == prof

    d.tox.sent.clear()
    d._on_friend_connection_status(5, 2)  # bob comes online: card first, then the queue
    assert _types(d) == ["profile"]
    assert d._get_profile({})["profile"] == prof


def test_daemon_loads_profile_file_on_start(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    paths.profile_path().write_text(json.dumps({"kind": "human", "name": "jeff", "x": 1,
                                                "proto": 1, "caps": ["profile"]}))
    d = object.__new__(Daemon)
    # proto/caps come from this build, not from the file (an upgrade must advertise new caps)
    assert d._load_profile() == {"kind": "human", "name": "jeff", "proto": envelope.VERSION,
                                 "caps": list(envelope.CAPS)}
    paths.profile_path().write_text("{broken")
    assert d._load_profile() == {}


# --- session labels & rate limit ---------------------------------------------

def test_incoming_session_label_is_stored_and_returned(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_friend_message(5, envelope.encode(envelope.make_text("hi", "powerup-chess")), 0)
    d._on_friend_message(5, envelope.encode(envelope.make_text("plain")), 0)
    msgs = d._get_messages({"alias": "bob"})["messages"]
    assert [m["from_session"] for m in msgs] == [None, "powerup-chess"]
    assert all("profile" in m and "profile_json" not in m for m in msgs)
    assert d._get_message({"msg_uuid": msgs[1]["msg_uuid"]})["message"]["from_session"] == "powerup-chess"


def test_outgoing_session_label_is_stored_and_sent(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    res = d._send_message({"alias": "bob", "body": "x" * 2000, "session": "toxi"})
    assert res["status"] == "sent" and res["parts"] == 2
    sent = [envelope.parse(p) for _, p in d.tox.sent]
    assert all(e["data"]["from_session"] == "toxi" for e in sent)
    rows = d.db.execute("SELECT from_session FROM messages WHERE direction='out'").fetchall()
    assert {r["from_session"] for r in rows} == {"toxi"}


def test_incoming_texts_are_rate_limited_per_contact(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    for i in range(70):
        d._on_friend_message(5, envelope.encode(envelope.make_text(f"m{i}")), 0)
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 60
    assert _types(d).count("ack") == 60  # dropped messages are not ACKed


# --- file modes --------------------------------------------------------------

def test_harden_permissions_sets_private_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    paths.ensure_media_dir()
    for f in (paths.tox_state_path(), paths.db_path(), paths.config_path()):
        f.write_bytes(b"x")
        f.chmod(0o644)
    os.chmod(tmp_path, 0o755)
    _harden_permissions()
    assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(paths.media_dir()).st_mode) == 0o700
    for f in (paths.tox_state_path(), paths.db_path(), paths.config_path()):
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
