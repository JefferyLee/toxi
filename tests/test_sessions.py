"""Session routing: several agent sessions share one identity; each peer message
goes to exactly one of them, or to the shared inbox."""
import json
import time

from click.testing import CliRunner

from toxi import peers, sessions
from toxi.cli import cli

from test_routing_done import _daemon, _incoming  # noqa: E402

LIVE = {101, 102, 103}


def _setup(tmp_path, monkeypatch, peer_cfg):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    monkeypatch.delenv("TOXI_SESSION", raising=False)
    (tmp_path / "config.toml").write_text(peer_cfg)
    monkeypatch.setattr(sessions, "_alive", lambda pid: pid in LIVE)


def _as(monkeypatch, pid):
    monkeypatch.setattr(sessions, "agent_pid", lambda: pid)


def test_names_are_unique_and_ended_sessions_go_away(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    a = sessions.register(str(tmp_path / "toxi"), pid=101)
    b = sessions.register(str(tmp_path / "toxi"), pid=102)
    assert (a["name"], b["name"]) == ("toxi", "toxi-2")
    assert sessions.register(str(tmp_path / "toxi"), pid=101) == a  # stable
    monkeypatch.setenv("TOXI_SESSION", "review me")
    assert sessions.register(str(tmp_path / "toxi"), pid=103)["name"] == "review-me"
    sessions.register(str(tmp_path / "x"), pid=999)  # its agent is gone
    assert [s["pid"] for s in sessions.live()] == [101, 102, 103]
    assert not (sessions._dir() / "999.json").exists()


def test_owner_picks_one_session_or_the_shared_inbox(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    sessions.register("/w/toxi", pid=101)
    sessions.register("/w/toxi", pid=102)                                 # toxi-2
    sessions.register("/w/chess", pid=103)
    live, now = sessions.live(), time.time()
    bound_toxi, bound_chess = {"dirs": ["/w/toxi"]}, {"dirs": ["/w/chess"]}
    plain = {"msg_type": "text", "content": "hi", "created_at": now}
    assert peers.owner(plain, bound_chess, live) == "chess"
    assert peers.owner(plain, bound_toxi, live) is None                   # two candidates
    assert peers.owner(plain, {"dirs": []}, live) is None                 # unbound, three live
    assert peers.owner(dict(plain, content="@Toxi-2 hi"), bound_chess, live) == "toxi-2"
    assert peers.owner(dict(plain, content="@later hi"), bound_chess, live) == "later"
    reply = dict(plain, route_dir="/w/toxi", route_session="toxi-2")
    assert peers.owner(reply, bound_chess, live) == "toxi-2"              # the asker, by name
    old = dict(reply, created_at=now - peers.ROUTE_TTL - 1)
    assert peers.owner(old, bound_chess, live) == "chess"                 # stale: dirs again
    assert peers.owner(plain, {"dirs": []}, live[:1]) == "toxi"           # the only session


def _msgs():
    now = int(time.time())
    return [{"msg_uuid": "3", "alias": "bob", "msg_type": "text", "content": "friend", "created_at": now},
            {"msg_uuid": "2", "alias": "phone", "msg_type": "text", "content": "@toxi-2 go", "created_at": now},
            {"msg_uuid": "1", "alias": "phone", "msg_type": "text", "content": "anyone?", "created_at": now}]


def test_take_and_split_never_cross_sessions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, '[peers.phone]\ndirs = ["/w/toxi"]\ntrust = "owner"\n')
    msgs, marked = _msgs(), []

    def request(method, params=None, **k):
        if method == "mark_read":
            marked.extend(params["msg_uuids"])
        return {"messages": [m for m in msgs if m["msg_uuid"] not in marked]}
    monkeypatch.setattr(peers.client, "request", request)
    sessions.register("/w/toxi", pid=101)
    sessions.register("/w/toxi", pid=102)
    _as(monkeypatch, 101)
    assert peers.take(cwd="/w/toxi") == []
    _as(monkeypatch, 102)
    assert [m["msg_uuid"] for m in peers.take(cwd="/w/toxi")] == ["2"]
    me = sessions.current(101)
    mine, shared = peers.split(_msgs(), me)
    assert mine == [] and [m["msg_uuid"] for m in shared] == ["3", "1"]   # "2" is toxi-2's
    assert peers.split(_msgs(), None) == (_msgs(), [])                    # no session: all


def test_unread_in_a_session_hides_the_shared_inbox_until_all(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, '[peers.phone]\ndirs = ["/w/toxi"]\ntrust = "owner"\n')
    sessions.register("/w/toxi", pid=101)
    sessions.register("/w/toxi", pid=102)
    _as(monkeypatch, 101)
    monkeypatch.setattr("toxi.cli._call", lambda m, p=None: {"messages": _msgs()} if m == "get_messages" else {})
    out = CliRunner().invoke(cli, ["unread"]).output
    assert "No unread messages." in out and "2 more in the shared inbox" in out
    out = CliRunner().invoke(cli, ["unread", "--all"]).output
    assert "friend" in out and "anyone?" in out and "@toxi-2 go" not in out
    assert json.loads(CliRunner().invoke(cli, ["--json", "unread"]).output) == []  # hooks' peek


def test_at_question_mark_from_the_owner_lists_sessions(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    _setup(tmp_path, monkeypatch, '[peers.codex]\ntrust = "owner"\n')
    sessions.register("/w/toxi", pid=101)
    sessions.register("/w/chess", pid=102)
    woke = []
    monkeypatch.setattr(d, "_stored", lambda *a: woke.append(a))
    _incoming(d, " @? ")
    rows = d.db.execute("SELECT msg_uuid, direction, content, reply_to, read_at FROM messages").fetchall()
    ask = next(r for r in rows if r["direction"] == "in")
    answer = next(r for r in rows if r["direction"] == "out")
    assert ask["read_at"] and not woke
    assert answer["content"] == "@toxi  /w/toxi\n@chess  /w/chess"
    assert answer["reply_to"] == ask["msg_uuid"]
    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "collaborator"\n')
    _incoming(d, "@?")
    assert woke  # only the owner's device gets the list
