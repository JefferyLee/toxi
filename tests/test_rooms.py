"""Design §4.5 rooms: app-layer fan-out with a member card, no transport group."""
import json
import selectors
from collections import deque

import pytest
from click.testing import CliRunner

from toxi import db, envelope
from toxi.cli import cli
from toxi.daemon import Daemon, RpcError

from test_events import FakeTox  # noqa: E402


def _daemon(tmp_path, monkeypatch, name=b"ada"):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FakeTox()
    d.tox.self_get_name = lambda: name
    d.tox.friend_get_connection_status = lambda fn: 2 if fn == 5 else 0  # carol (6) is offline
    d.profile = {}
    d.ack_timeout, d.fail_after = 300, 86400
    d._text_times, d._request_times = {}, deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts, d._sel = [], {}, selectors.DefaultSelector()
    d._dropped_parts = deque(maxlen=200)
    d._transfers, d._out, d._file_meta = {}, {}, {}
    caps = json.dumps({"kind": "agent", "name": "Bob", "caps": list(envelope.CAPS)})
    d.db.executemany(
        "INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online, profile_json) "
        "VALUES (?, ?, 1, ?, ?, ?)",
        [("BB" * 32, "bob", 5, 1, caps), ("CC" * 32, "carol", 6, 0, None)])
    d.db.commit()
    return d


def _sent(d, etype):
    return [(fn, e) for fn, e in d.tox.sent if e["type"] == etype]


def test_create_room_sends_cards_and_lists_members(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    res = d._create_room({"name": "ops", "aliases": ["bob", "carol"]})
    assert res["members"] == 3 and res["card_sent_to"] == ["bob"]  # carol is offline
    (fn, card), = _sent(d, "room")
    assert fn == 5 and card["data"]["name"] == "ops"
    assert {m["pk"]: m["name"] for m in card["data"]["members"]} == {
        "A" * 64: "ada", "BB" * 32: "Bob", "CC" * 32: "carol"}  # profile name, else alias
    room = d._get_room({"room": "ops"})["room"]
    assert room["mine"] is True and [m["alias"] for m in room["members"]] == [None, "bob", "carol"]
    assert room["members"][0]["me"] is True
    # Carol gets the card when she connects.
    d.tox.sent.clear()
    d._on_friend_connection_status(6, 2)
    assert [e["data"]["id"] for _, e in _sent(d, "room")] == [res["room_id"]]
    with pytest.raises(RpcError):
        d._create_room({"name": "x", "aliases": ["nobody"]})


def test_receive_card_only_from_creator_and_unknown_members_are_flagged(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    card = envelope.make_room("R1", "chess", [
        {"pk": "BB" * 32, "name": "Bob"}, {"pk": "A" * 64, "name": "ada"}, {"pk": "DD" * 32, "name": "Dan"}])
    d._on_friend_message(5, envelope.encode(card), 0)  # from bob (friend 5)
    room = d._get_room({"room": "R1"})["room"]
    assert room["mine"] is False and room["name"] == "chess"
    views = {m["name"]: (m["alias"], m["me"]) for m in room["members"]}
    assert views == {"Bob": ("bob", False), "ada": (None, True), "Dan": (None, False)}
    # Someone else re-sending a card for the same room is ignored.
    forged = envelope.make_room("R1", "chess!", [{"pk": "CC" * 32, "name": "x"}])
    d._on_friend_message(6, envelope.encode(forged), 0)
    assert d._get_room({"room": "R1"})["room"]["name"] == "chess"
    assert d._list_rooms({})["rooms"][0]["members"] == 3
    assert envelope.clean_room({"id": "x", "name": "y", "members": [{"pk": "short"}]}) is None


def test_creator_adds_and_removes_members(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    rid = d._create_room({"name": "ops", "aliases": ["bob"]})["room_id"]
    d.tox.sent.clear()
    res = d._room_members_rpc({"room": "ops", "add": ["carol"]})
    assert {m["name"] for m in res["members"]} == {"ada", "Bob", "carol"} and res["card_sent_to"] == ["bob"]
    cards = [e for _, e in _sent(d, "room")]
    assert len(cards) == 1 and len(cards[0]["data"]["members"]) == 3
    d._room_members_rpc({"room": rid[:8], "remove": ["carol"]})
    assert {m["name"] for m in d._get_room({"room": "ops"})["room"]["members"]} == {"ada", "Bob"}
    with pytest.raises(RpcError):
        d._room_members_rpc({"room": "ops", "add": ["stranger"]})
    # A member (not the creator) cannot change the list; a card that drops me removes the room.
    d2 = _daemon(tmp_path / "member", monkeypatch)
    d2._on_friend_message(5, envelope.encode(envelope.make_room("R1", "chess", [
        {"pk": "BB" * 32, "name": "Bob"}, {"pk": "A" * 64, "name": "ada"}])), 0)
    with pytest.raises(RpcError):
        d2._room_members_rpc({"room": "R1", "add": ["carol"]})
    d2._on_friend_message(5, envelope.encode(envelope.make_room("R1", "chess", [
        {"pk": "BB" * 32, "name": "Bob"}])), 0)
    assert d2._list_rooms({})["rooms"] == []


def test_send_room_fans_out_and_collapses_in_history(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    rid = d._create_room({"name": "ops", "aliases": ["bob", "carol"]})["room_id"]
    d.tox.sent.clear()
    res = d._send_room({"room": "ops", "body": "standup?", "thread": "daily", "session": "s"})
    assert [(s["alias"], s["status"]) for s in res["sent"]] == [("bob", "sent"), ("carol", "queued")]
    assert res["unreachable"] == []
    (fn, env), = _sent(d, "text")
    assert fn == 5 and env["data"]["room"] == rid and env["data"]["room_msg"] == res["room_msg_id"]
    assert env["data"]["thread"] == "daily"
    hist = d._get_messages({"room": "ops"})["messages"]
    assert len(hist) == 1 and hist[0]["recipients"] == [{"alias": "bob", "status": "sent"},
                                                         {"alias": "carol", "status": "queued"}]
    assert hist[0]["status"] == "queued" and hist[0]["room_name"] == "ops"
    assert len(d._get_messages({"alias": "bob"})["messages"]) == 1
    # Carol comes online: her copy is flushed with the room fields intact.
    d.tox.sent.clear()
    d._on_friend_connection_status(6, 2)
    texts = _sent(d, "text")
    assert len(texts) == 1 and texts[0][1]["data"]["room"] == rid


def test_incoming_room_message_is_tagged(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_friend_message(5, envelope.encode(envelope.make_room("R9", "chess", [
        {"pk": "BB" * 32, "name": "Bob"}, {"pk": "A" * 64, "name": "ada"}])), 0)
    d._on_friend_message(5, envelope.encode(envelope.make_text(
        "your move", meta={"room": "R9", "room_msg": "m1", "kind": "request"})), 0)
    m = d._get_messages({"room": "R9"})["messages"][0]
    assert m["room_id"] == "R9" and m["room_name"] == "chess" and m["kind"] == "request"
    from toxi import peers
    assert "(room chess · request)" in peers.as_prompt([dict(m, trust="observer")])
    with pytest.raises(RpcError):
        d._get_messages({"room": "nope"})


def test_cli_room_commands(monkeypatch):
    calls = []

    def request(method, params=None, **k):
        calls.append((method, params))
        return {
            "create_room": {"room_id": "abcdef12-x", "name": "ops", "members": 3, "card_sent_to": ["bob"]},
            "list_rooms": {"rooms": [{"room_id": "abcdef12-x", "name": "ops", "members": 3, "unread": 2,
                                      "last_at": None, "mine": True}]},
            "get_room": {"room": {"room_id": "abcdef12-x", "name": "ops", "mine": True, "members": [
                {"name": "ada", "alias": None, "me": True, "online": False, "public_key": "A" * 64},
                {"name": "Bob", "alias": "bob", "me": False, "online": True, "public_key": "B" * 64},
                {"name": "Dan", "alias": None, "me": False, "online": False, "public_key": "D" * 64}]}},
            "send_room": {"room_msg_id": "m", "room_id": "abcdef12-x", "name": "ops",
                          "sent": [{"alias": "bob", "status": "sent"}], "unreachable": ["Dan"]},
            "get_messages": {"messages": []},
        }[method]

    monkeypatch.setattr("toxi.client.request", request)
    r = CliRunner()
    out = r.invoke(cli, ["room", "create", "ops", "bob", "carol"]).output
    assert "room ops (abcdef12) with 3 members; card sent to: bob" in out
    assert calls[-1][1] == {"name": "ops", "aliases": ["bob", "carol"]}
    assert "ops" in r.invoke(cli, ["room", "list"]).output and "📬 2" in r.invoke(cli, ["room", "list"]).output
    out = r.invoke(cli, ["room", "show", "ops"]).output
    assert "(you)" in out and "✓ bob (Bob)" in out and "✗ Dan — not your contact" in out
    out = r.invoke(cli, ["room", "send", "ops", "hi", "-t", "daily", "-k", "request"]).output
    assert "✓ ops: bob (sent)" in out and "skipped: Dan" in out
    assert calls[-1][1]["thread"] == "daily" and calls[-1][1]["kind"] == "request"
    r.invoke(cli, ["read", "-", "--room", "ops"])
    assert calls[-1][1] == {"limit": 20, "thread": None, "room": "ops"}
