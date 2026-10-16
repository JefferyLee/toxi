"""Reply routing to the session that asked, and `done` (processing) receipts."""
import asyncio
import json
import selectors
import time
from collections import deque

from click.testing import CliRunner

from toxi import db, envelope, mcp_server, peers
from toxi.cli import cli
from toxi.daemon import Daemon

from test_events import FakeTox, _events, _subscriber  # noqa: E402


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


def _incoming(d, body, **meta):
    d._on_friend_message(5, envelope.encode(envelope.make_text(body, meta=meta)), 0)
    return d.db.execute("SELECT msg_uuid FROM messages ORDER BY id DESC LIMIT 1").fetchone()[0]


# --- route_dir ---------------------------------------------------------------

def test_reply_and_thread_route_to_the_asking_sessions_dir(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = d._send_message({"alias": "codex", "body": "review?", "thread": "pr-1",
                           "session_dir": "/w/those-moments"})
    d._send_message({"alias": "codex", "body": "unrelated", "session_dir": "/w/other"})
    a = _incoming(d, "LGTM", reply_to=req["msg_uuid"])                   # explicit reply
    b = _incoming(d, "one more thing", thread="pr-1")                    # same thread
    c = _incoming(d, "hello?")                                           # neither
    rows = {m["msg_uuid"]: m for m in d._get_messages({"alias": "codex", "unread_only": True})["messages"]}
    assert rows[a]["route_dir"] == "/w/those-moments"
    assert rows[b]["route_dir"] == "/w/those-moments"
    assert rows[c]["route_dir"] is None
    assert d._get_message({"msg_uuid": a})["message"]["route_dir"] == "/w/those-moments"
    # a reply to one part of a multi-part send routes too
    big = d._send_message({"alias": "codex", "body": "z" * 3000, "session_dir": "/w/big"})
    x = _incoming(d, "got the big one", reply_to=big["msg_uuid"])
    assert d._get_message({"msg_uuid": x})["message"]["route_dir"] == "/w/big"


def test_routed_to_prefers_fresh_route_then_dirs(tmp_path):
    spec = {"dirs": ["/w/bound"], "trust": "observer"}
    now = time.time()
    fresh = {"route_dir": "/w/asker", "created_at": now}
    stale = {"route_dir": "/w/asker", "created_at": now - peers.ROUTE_TTL - 1}
    plain = {"route_dir": None, "created_at": now}
    assert peers.routed_to(fresh, "/w/asker", spec) and not peers.routed_to(fresh, "/w/bound", spec)
    assert peers.routed_to(stale, "/w/bound", spec) and not peers.routed_to(stale, "/w/asker", spec)
    assert peers.routed_to(plain, "/w/bound", spec) and not peers.routed_to(plain, "/w/elsewhere", spec)
    assert peers.routed_to(plain, "/w/anywhere", {"dirs": [], "trust": "observer"})
    assert peers.routed_to(fresh, None, spec)  # no session context: take everything


def test_take_uses_route_dir_over_peer_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[peers.codex]\ndirs = ["/w/bound"]\ntrust = "collaborator"\n')
    now = int(time.time())
    msgs = [{"msg_uuid": "2", "alias": "codex", "msg_type": "text", "content": "reply", "created_at": now,
             "route_dir": "/w/asker"},
            {"msg_uuid": "1", "alias": "codex", "msg_type": "text", "content": "plain", "created_at": now,
             "route_dir": None}]
    monkeypatch.setattr(peers.client, "request",
                        lambda m, p=None, **k: {"messages": msgs} if m == "get_messages" else {"marked": 0})
    assert [m["msg_uuid"] for m in peers.take(cwd="/w/asker")] == ["2"]   # not bound, but asked
    assert [m["msg_uuid"] for m in peers.take(cwd="/w/bound")] == ["1"]   # bound, but not the asker
    assert [m["msg_uuid"] for m in peers.take(cwd="/w/elsewhere")] == []
    assert [m["msg_uuid"] for m in peers.take()] == ["1", "2"]            # no cwd: everything


def test_mcp_send_passes_session_dir_and_wait_listens_to_all_peers(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/w/asker")
    (tmp_path / "config.toml").write_text('[peers.codex]\ndirs = ["/w/bound"]\ntrust = "observer"\n'
                                          '[peers.bot]\ntrust = "observer"\n')
    sent = {}
    monkeypatch.setattr(mcp_server.client, "request",
                        lambda m, p=None, **k: sent.update(p) or {"msg_uuid": "u", "status": "sent"})
    asyncio.run(mcp_server.send_message("codex", "hi", thread="t"))
    assert sent["session_dir"] == "/w/asker"
    seen = {}

    async def fake_sub(aliases=None, context=0, *, deadline=None, sock_path=None):
        seen["aliases"] = aliases
        yield {"event": "message.new", "data": {"direction": "in", "alias": "codex"}}

    monkeypatch.setattr(mcp_server.client, "asubscribe", fake_sub)
    calls = []

    def take(alias=None, cwd=None, thread=None):
        calls.append(cwd)
        return [] if len(calls) == 1 else [{"msg_uuid": "1", "alias": "codex", "content": "r",
                                            "trust": "observer"}]

    monkeypatch.setattr(mcp_server.peers, "take", take)
    res = asyncio.run(mcp_server.wait_for_message(timeout=2))
    assert calls == ["/w/asker", "/w/asker"]
    assert sorted(seen["aliases"]) == ["bot", "codex"] and res["messages"]


# --- done receipts -------------------------------------------------------------

def test_reply_with_reply_to_sends_done_and_marks_handled(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    q = _incoming(d, "can you deploy?", kind="request")
    d.tox.sent.clear()
    d._send_message({"alias": "codex", "body": "deployed", "reply_to": q, "kind": "report"})
    receipts = [e["data"] for _, e in d.tox.sent if e["type"] == "receipt"]
    assert receipts == [{"ref_uuid": q, "kind": "done"}]
    row = d.db.execute("SELECT handled_at, read_at, status FROM messages WHERE msg_uuid=?", (q,)).fetchone()
    assert row["handled_at"] and row["read_at"] and row["status"] == "read"
    # a second reply does not send another `done`
    d.tox.sent.clear()
    d._send_message({"alias": "codex", "body": "and verified", "reply_to": q})
    assert not [e for _, e in d.tox.sent if e["type"] == "receipt"]


def test_mark_handled_rpc_and_incoming_done_receipt(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    b = _subscriber(d)
    q = _incoming(d, "fyi", kind="report")
    d.tox.sent.clear()
    assert d._mark_handled({"msg_uuids": [q, "nope"]}) == {"handled": 1}
    assert [e["data"]["kind"] for _, e in d.tox.sent if e["type"] == "receipt"] == ["done"]
    assert d._mark_handled({"msg_uuids": [q]}) == {"handled": 0}
    # sender side: done implies read + delivered, and read never downgrades handled
    out = d._send_message({"alias": "codex", "body": "please", "kind": "request"})
    _events(b)
    d._on_friend_message(5, envelope.encode(envelope.make_receipt(out["msg_uuid"], "done")), 0)
    m = d._get_message({"msg_uuid": out["msg_uuid"]})["message"]
    assert m["status"] == "handled" and m["handled_at"] and m["read_at"] is None
    assert [e["data"]["kind"] for e in _events(b) if e["event"] == "receipt"] == ["done"]
    d._on_friend_message(5, envelope.encode(envelope.make_receipt(out["msg_uuid"], "read")), 0)
    assert d._get_message({"msg_uuid": out["msg_uuid"]})["message"]["status"] == "handled"
    threads = d._list_threads({})["threads"]
    assert threads == [] or all(t["open_requests"] == 0 for t in threads)
    d._send_message({"alias": "codex", "body": "another", "kind": "request", "thread": "ops"})
    t = next(t for t in d._list_threads({})["threads"] if t["thread"] == "ops")
    assert t["open_requests"] == 1


def test_cli_done_and_wait_handled(monkeypatch):
    calls = []
    seen = {"n": 0}

    def request(method, params=None, **k):
        calls.append((method, params))
        if method == "send_message":
            return {"msg_uuid": "u", "status": "sent"}
        if method == "get_message":
            seen["n"] += 1
            return {"message": {"status": ["read", "read", "handled"][min(seen["n"] - 1, 2)]}}
        return {"handled": len(params["msg_uuids"])}

    monkeypatch.setattr("toxi.client.request", request)
    out = CliRunner().invoke(cli, ["done", "a", "b"]).output
    assert "2 message(s) marked handled" in out and calls[-1] == ("mark_handled", {"msg_uuids": ["a", "b"]})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/w/asker")
    r = CliRunner().invoke(cli, ["send", "codex", "go", "--wait-handled", "5"])
    assert "✓ handled by codex" in r.output
    assert next(p for m, p in calls if m == "send_message")["session_dir"] == "/w/asker"
