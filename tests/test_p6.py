"""Backlog items 3 and 4: room history for newcomers; archive and search."""
import json
import selectors
import time
from collections import deque

import pytest
from click.testing import CliRunner

from toxi import db, envelope, paths, peers
from toxi.cli import _parse_age, cli
from toxi.daemon import ROOM_HISTORY, Daemon, RpcError

from test_events import FakeTox  # noqa: E402


def _daemon(tmp_path, monkeypatch):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FakeTox()
    d.tox.self_get_name = lambda: b"ada"
    d.profile = {}
    d.ack_timeout, d.fail_after = 300, 86400
    d._started_at, d._last_archive = time.time(), 0.0
    d._text_times, d._request_times = {}, deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts, d._sel = [], {}, selectors.DefaultSelector()
    d._dropped_parts = deque(maxlen=200)
    d._transfers, d._out, d._file_meta = {}, {}, {}
    caps = json.dumps({"kind": "agent", "name": "Bob", "caps": list(envelope.CAPS)})
    d.db.executemany(
        "INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online, profile_json) "
        "VALUES (?, ?, 1, ?, 1, ?)",
        [("BB" * 32, "bob", 5, caps), ("CC" * 32, "carol", 6, None)])
    d.db.commit()
    return d


def _texts(d, fn=None):
    return [e for f, e in d.tox.sent if e["type"] == "text" and (fn is None or f == fn)]


# --- room history ------------------------------------------------------------

def test_new_member_gets_relayed_history_once(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._create_room({"name": "ops", "aliases": ["bob"]})
    rid = d._get_room({"room": "ops"})["room"]["room_id"]
    a = d._send_room({"room": "ops", "body": "first", "thread": "t"})
    d._on_friend_message(5, envelope.encode(envelope.make_text(
        "bob says hi", meta={"room": rid, "room_msg": "bm1", "kind": "report"})), 0)
    d._send_room({"room": "ops", "body": "x" * 2000})  # long: history copy goes out in parts
    d.tox.sent.clear()
    d._room_members_rpc({"room": "ops", "add": ["carol"]})
    to_carol = _texts(d, 6)
    assert [e["data"]["relayed"]["name"] for e in to_carol][:2] == ["ada", "Bob"]  # oldest first
    assert to_carol[0]["uuid"] == a["room_msg_id"] and to_carol[0]["data"]["room_msg"] == a["room_msg_id"]
    assert to_carol[0]["data"]["thread"] == "t" and to_carol[0]["data"]["room"] == rid
    assert to_carol[1]["data"]["kind"] == "report" and to_carol[1]["data"]["relayed"]["ts"]
    parts = [e for e in to_carol if e["data"].get("part")]
    assert parts and parts[0]["data"]["part"]["n"] == len(parts) and all(len(envelope.encode(e)) <= 1372 for e in parts)
    assert d.db.execute("SELECT COUNT(*) c FROM messages WHERE relayed IS NOT NULL").fetchone()["c"] == 0  # no local rows
    # already caught up: neither a second add nor a reconnect resends
    d.tox.sent.clear()
    d._send_room_cards(6)
    assert _texts(d, 6) == []
    d._room_members_rpc({"room": "ops", "add": ["carol"]})
    assert _texts(d, 6) == []


def test_history_is_capped_and_skips_what_the_member_already_has(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._create_room({"name": "ops", "aliases": ["bob"]})
    for i in range(ROOM_HISTORY + 5):
        d._send_room({"room": "ops", "body": f"m{i}"})
    d.tox.sent.clear()
    d._room_members_rpc({"room": "ops", "add": ["carol"]})  # newcomer: the newest ROOM_HISTORY
    bodies = [e["data"]["body"] for e in _texts(d, 6)]
    assert len(bodies) == ROOM_HISTORY and bodies[0] == "m5" and bodies[-1] == f"m{ROOM_HISTORY + 4}"
    # bob was the recipient of every one of those: a late "catch-up" sends him nothing
    d.db.execute("UPDATE room_members SET history_sent=NULL WHERE public_key=?", ("BB" * 32,))
    d.db.commit()
    d.tox.sent.clear()
    d._send_room_cards(5)
    assert _texts(d, 5) == []


def test_receiver_stores_history_as_read_and_tags_it(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_friend_message(5, envelope.encode(envelope.make_room("R1", "chess", [
        {"pk": "BB" * 32, "name": "Bob"}, {"pk": "A" * 64, "name": "ada"}])), 0)
    env = envelope.make_text("old news", meta={"room": "R1", "room_msg": "orig1",
                                               "relayed": {"name": "Carol", "ts": 1700000000}})
    d._on_friend_message(5, envelope.encode(env), 0)
    m = d._get_message({"msg_uuid": env["uuid"]})["message"]
    assert m["relayed"] == {"name": "Carol", "ts": 1700000000} and m["read_at"]
    assert d._hook_msgs == [] and not paths.inbox_flag_path().exists()
    assert "history: Carol @" in peers.meta_tag(m) and "room chess" in peers.meta_tag(m)
    assert d._get_messages({"unread_only": True})["messages"] == []


# --- archive & search ------------------------------------------------------------

def _seed(d, p=""):
    now = int(time.time())
    old = now - 200 * 86400
    rows = [(p + "o1", 1, "in", "ancient report", old, "read"), (p + "o2", 1, "out", "ancient reply", old + 1, "delivered"),
            (p + "o3", 2, "in", "ancient carol", old + 2, "read"), (p + "q1", 1, "out", "never delivered", old + 3, "sent"),
            (p + "n1", 1, "in", "recent report", now - 60, "read")]
    d.db.executemany("INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, created_at, "
                     "status, thread) VALUES (?, ?, ?, 'text', ?, ?, ?, 'ops')", rows)
    d.db.commit()


def test_archive_moves_old_delivered_rows_and_search_covers_both(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    _seed(d)
    assert d._archive_messages({"older_than_days": 90, "dry_run": True})["matched"] == 3
    assert d._archive_messages({"older_than_days": 90, "alias": "carol", "dry_run": True})["matched"] == 1
    res = d._archive_messages({"older_than_days": 90})
    assert res["matched"] == 3 and (tmp_path / "archive.db").exists()
    left = {r[0] for r in d.db.execute("SELECT msg_uuid FROM messages")}
    assert left == {"q1", "n1"}  # undelivered and recent rows stay
    st = d._archive_status({})
    assert st["messages"] == 2 and st["archived"] == 3 and st["archive_bytes"] > 0
    found = d._search_messages({"q": "ANCIENT"})["messages"]
    assert found == []
    found = d._search_messages({"q": "ANCIENT", "archived": True})["messages"]
    assert sorted(m["msg_uuid"] for m in found) == ["o1", "o2", "o3"] and all(m["archived"] for m in found)
    assert [m["alias"] for m in d._search_messages({"q": "ancient", "archived": True, "alias": "carol"})["messages"]] == ["carol"]
    assert d._search_messages({"q": "report"})["messages"][0]["msg_uuid"] == "n1"
    with pytest.raises(RpcError):
        d._search_messages({"q": "  "})
    with pytest.raises(RpcError):
        d._archive_messages({"older_than_days": 0})
    # archiving again is a no-op; delete mode removes without archiving
    assert d._archive_messages({"older_than_days": 90})["matched"] == 0
    _seed(d, "b")
    assert d._archive_messages({"older_than_days": 90, "delete": True})["matched"] == 3
    assert d._archive_status({})["archived"] == 3


def test_daily_auto_archive_uses_config(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    _seed(d)
    d._archive_tick()
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 5  # off by default
    (tmp_path / "config.toml").write_text("[archive]\nafter_days = 30\n")
    d._last_archive = 0.0
    d._archive_tick()
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 2
    d._last_archive = time.time()
    _seed(d, "c")
    d._archive_tick()  # not due yet
    assert d.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 7


def test_cli_archive_and_search(monkeypatch):
    assert (_parse_age("90d"), _parse_age("2w"), _parse_age("6m"), _parse_age("1y"), _parse_age("45")) == (90, 14, 180, 365, 45)
    calls = []

    def request(method, params=None, **k):
        calls.append((method, params))
        params = params or {}
        return {"archive_messages": {"matched": 12, "cutoff": 1, "archive": "a.db", "dry_run": params.get("dry_run"), "deleted": False},
                "archive_status": {"messages": 100, "oldest": int(time.time()) - 86400, "db_bytes": 4096,
                                   "archived": 7, "archive_bytes": 2048, "auto_after_days": 0},
                "search_messages": {"count": 1, "messages": [{"msg_uuid": "u", "alias": "bob", "direction": "in",
                                                              "content": "ancient report", "created_at": int(time.time()),
                                                              "thread": "ops", "archived": True}]}}[method]

    monkeypatch.setattr("toxi.client.request", request)
    r = CliRunner()
    assert "12 message(s) older than 90 days would be archived" in r.invoke(cli, ["archive", "--dry-run"]).output
    assert calls[-1][1]["older_than_days"] == 90
    assert "moved to archive.db" in r.invoke(cli, ["archive", "--older-than", "6m"]).output
    assert calls[-1][1]["older_than_days"] == 180
    assert "archive.db: 7 messages" in r.invoke(cli, ["archive", "--status"]).output
    out = r.invoke(cli, ["search", "ancient", "--archived", "--alias", "bob"]).output
    assert "bob (" in out and "#ops (archived): ancient report" in out
    assert calls[-1][1] == {"q": "ancient", "alias": "bob", "thread": None, "limit": 20, "archived": True}
    from toxi import mcp_server
    monkeypatch.setattr(mcp_server.client, "request", request)
    assert mcp_server.search_messages("ancient", archived=True)["count"] == 1
