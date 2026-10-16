"""Design §4.5: thread / reply_to / kind / needs_human / tags on texts, acks that
never wake, thread listing, and how peers render them."""
import asyncio
import json
import selectors
import time
from collections import deque

from click.testing import CliRunner

from toxi import db, envelope, mcp_server, paths, peers
from toxi.cli import cli
from toxi.daemon import Daemon

from test_events import FakeTox, _events, _subscriber  # noqa: E402  (shared fixtures)


def _daemon(tmp_path, monkeypatch):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FakeTox()
    d.profile = {}
    d.ack_timeout, d.fail_after = 300, 86400
    d._text_times, d._request_times = {}, deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts, d._sel = [], {}, selectors.DefaultSelector()
    d._dropped_parts = deque(maxlen=200)
    d._transfers, d._out, d._file_meta = {}, {}, {}
    prof = json.dumps({"kind": "agent", "caps": list(envelope.CAPS)})
    d.db.execute("INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online, "
                 "profile_json) VALUES ('CC', 'codex', 1, 5, 1, ?)", (prof,))
    d.db.commit()
    return d


def test_clean_meta_validates_and_bounds():
    assert envelope.clean_meta(None) == {} and envelope.clean_meta({"kind": "msg"}) == {}
    m = envelope.clean_meta({"thread": " pr-1 ", "reply_to": "u" * 80, "kind": "REQUEST",
                             "needs_human": 1, "tags": ["WARN", "", 7] + ["x"] * 10})
    assert m["thread"] == "pr-1" and len(m["reply_to"]) == 64 and m["kind"] == "request"
    assert m["needs_human"] is True and m["tags"] == ["WARN", "7"] + ["x"] * 5  # 8 slots, blanks dropped
    assert envelope.clean_meta({"kind": "bogus", "needs_human": False}) == {}


def test_outgoing_meta_is_stored_sent_and_kept_on_resend(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    res = d._send_message({"alias": "codex", "body": "please review", "thread": "pr-1",
                           "kind": "request", "needs_human": True, "tags": ["urgent"]})
    data = d.tox.sent[0][1]["data"]
    assert data["thread"] == "pr-1" and data["kind"] == "request" and data["needs_human"] is True
    assert data["tags"] == ["urgent"] and "reply_to" not in data
    m = d._get_message({"msg_uuid": res["msg_uuid"]})["message"]
    assert (m["thread"], m["kind"], m["needs_human"], m["tags"]) == ("pr-1", "request", True, ["urgent"])
    # Resends (queue flush / retry) re-encode the same fields.
    d.db.execute("UPDATE messages SET status='queued'")
    d.db.commit()
    d.tox.sent.clear()
    d._flush_queue(1, 5)
    assert d.tox.sent[0][1]["data"]["thread"] == "pr-1"
    # Long bodies: meta rides on every part and the budget still holds.
    d.tox.sent.clear()
    d._send_message({"alias": "codex", "body": "z" * 3000, "thread": "t" * 64, "tags": ["a" * 24] * 8})
    assert len(d.tox.sent) > 1
    assert all(len(envelope.encode(e)) <= 1372 and e["data"]["thread"] == "t" * 64 for _, e in d.tox.sent)


def test_incoming_meta_stored_and_thread_filter(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    for i, meta in enumerate([{"thread": "a"}, {"thread": "b", "kind": "report"}, {}]):
        d._on_friend_message(5, envelope.encode(envelope.make_text(f"m{i}", meta=meta)), 0)
    all_msgs = d._get_messages({"alias": "codex"})["messages"]
    assert [m["kind"] for m in all_msgs] == ["msg", "report", "msg"]  # NULL kind reads as msg
    assert [m["content"] for m in d._get_messages({"thread": "a"})["messages"]] == ["m0"]
    threads = d._list_threads({})["threads"]
    assert {(t["thread"], t["count"], t["unread"]) for t in threads} == {("a", 1, 1), ("b", 1, 1)}


def test_parts_carry_meta_from_the_first_part(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    for i, c in enumerate(["aa", "bb"], 1):
        d._on_friend_message(5, envelope.encode(envelope.make_text(
            c, part={"id": "P", "i": i, "n": 2}, meta={"thread": "big", "kind": "report"})), 0)
    m = d._get_message({"msg_uuid": "P"})["message"]
    assert m["content"] == "aabb" and m["thread"] == "big" and m["kind"] == "report"


def test_ack_is_stored_but_never_wakes(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    b = _subscriber(d)
    d._on_friend_message(5, envelope.encode(envelope.make_text("got it", meta={"kind": "ack"})), 0)
    assert d._hook_msgs == [] and not paths.inbox_flag_path().exists()
    assert [e["event"] for e in _events(b)] == ["message.new"]  # subscribers still see it
    d._on_friend_message(5, envelope.encode(envelope.make_text("real one")), 0)
    assert len(d._hook_msgs) == 1 and paths.inbox_flag_path().exists()


def test_peers_take_skips_acks_and_filters_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "collaborator"\n')
    msgs = [{"msg_uuid": "3", "alias": "codex", "msg_type": "text", "content": "ok", "kind": "ack"},
            {"msg_uuid": "2", "alias": "codex", "msg_type": "text", "content": "do it", "kind": "request",
             "thread": "pr-1", "needs_human": True, "reply_to": "abcdef123456"},
            {"msg_uuid": "1", "alias": "codex", "msg_type": "text", "content": "Jeff said yes",
             "kind": "relay", "tags": ["WARN"]}]
    calls = []

    def request(method, params=None, **k):
        calls.append((method, params))
        if method == "get_messages":
            return {"messages": [m for m in msgs if not params.get("thread") or m.get("thread") == params["thread"]]}
        return {"marked": len(params["msg_uuids"])}

    monkeypatch.setattr(peers.client, "request", request)
    got = peers.take()
    assert [m["msg_uuid"] for m in got] == ["1", "2"]  # the ack is consumed, not delivered
    assert calls[1] == ("mark_read", {"msg_uuids": ["1", "2", "3"]})
    text = peers.as_prompt(got)
    assert "- codex (relay · WARN): (relayed by the sender, unverified) Jeff said yes" in text
    assert "- codex (#pr-1 · request · needs your decision · ↩ abcdef12): do it" in text
    assert "needs your decision` is for the user" in text
    assert [m["msg_uuid"] for m in peers.take(thread="pr-1")] == ["2"]
    assert calls[-2][1]["thread"] == "pr-1"


def test_cli_send_read_threads(monkeypatch):
    calls = []

    def request(method, params=None, **k):
        calls.append((method, params))
        return {"send_message": {"msg_uuid": "u", "status": "sent"},
                "get_messages": {"messages": []},
                "list_threads": {"threads": [{"alias": "codex", "thread": "pr-1", "count": 4,
                                              "last_at": int(time.time()), "unread": 2, "needs_human": 1}]},
                }[method]

    monkeypatch.setattr("toxi.client.request", request)
    r = CliRunner().invoke(cli, ["send", "codex", "go", "-t", "pr-1", "-k", "request", "--needs-human",
                                 "--tag", "WARN", "--tag", "ops", "-r", "u0"])
    assert r.exit_code == 0, r.output
    p = calls[-1][1]
    assert (p["thread"], p["kind"], p["needs_human"], p["tags"], p["reply_to"]) == \
        ("pr-1", "request", True, ["WARN", "ops"], "u0")
    assert CliRunner().invoke(cli, ["send", "codex", "x", "-k", "nope"]).exit_code != 0
    CliRunner().invoke(cli, ["read", "codex", "-t", "pr-1"])
    assert calls[-1][1]["thread"] == "pr-1"
    out = CliRunner().invoke(cli, ["threads"]).output
    assert "#pr-1" in out and "📬 2" in out and "⚑ 1 needs your decision" in out


def test_mcp_send_and_wait_carry_thread(monkeypatch, tmp_path):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "observer"\n')
    sent = {}
    monkeypatch.setattr(mcp_server.client, "request",
                        lambda m, p=None, **k: sent.update(p) or {"msg_uuid": "u", "status": "sent"})
    asyncio.run(mcp_server.send_message("codex", "hi", thread="pr-1", kind="report", tags=["x"]))
    assert sent["thread"] == "pr-1" and sent["kind"] == "report" and sent["tags"] == ["x"]
    seen = []
    monkeypatch.setattr(mcp_server.peers, "take",
                        lambda alias=None, cwd=None, thread=None: seen.append(thread) or
                        [{"msg_uuid": "1", "alias": "codex", "content": "r", "trust": "observer"}])
    res = asyncio.run(mcp_server.wait_for_message(thread="pr-1", timeout=1))
    assert seen == ["pr-1"] and res["messages"][0]["content"] == "r"
