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


def _claude_name(tmp_path, pid, name, source="user"):
    d = sessions.Path(sessions.os.environ["CLAUDE_CONFIG_DIR"]) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({"pid": pid, "name": name, "nameSource": source}))


def test_claude_session_name_wins_and_comes_back_when_freed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    cwd = str(tmp_path / "those-moments")
    _claude_name(tmp_path, 102, "those-moments-op", source="auto")  # not the user's
    assert sessions.register(cwd, pid=102)["name"] == "those-moments"
    _claude_name(tmp_path, 101, "those moments op")
    old = sessions.register(cwd, pid=101)
    assert old["name"] == "those-moments-op"
    _claude_name(tmp_path, 103, "those-moments-op")  # started later, same name
    new = sessions.register(cwd, pid=103)
    assert new["name"] == "those-moments-op-2"
    LIVE.discard(101)  # the old one ends
    try:
        again = sessions.register(cwd, pid=103)
    finally:
        LIVE.add(101)
    assert again["name"] == "those-moments-op" and again["since"] == new["since"]
    _claude_name(tmp_path, 102, "website")  # /rename while it runs
    assert sessions.register(cwd, pid=102)["name"] == "website"


def test_a_renamed_session_keeps_its_old_name_for_routing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    cwd = str(tmp_path / "those-moments")
    sessions.register(cwd, pid=101)  # those-moments
    assert sessions.register(cwd, pid=102)["name"] == "those-moments-2"
    _claude_name(tmp_path, 101, "website")
    assert sessions.register(cwd, pid=101) == {**sessions.current(101), "former": ["those-moments"]}
    # 101 still lives, so 102 does not take its old name, and a reply to
    # something 101 sent as `those-moments` still reaches 101.
    assert sessions.register(cwd, pid=102)["name"] == "those-moments-2"
    live = sessions.live()
    reply = {"msg_type": "text", "content": "ok", "route_dir": cwd,
             "route_session": "those-moments", "created_at": time.time()}
    assert peers.owner(reply, None, live) == "website"
    assert peers.owner({"msg_type": "text", "content": "@those-moments hi"}, None, live) == "website"


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


def test_cli_send_from_a_session_shell_carries_its_name_and_dir(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)  # Claude Code's Bash tool
    sessions.register("/w/toxi", pid=101)
    sessions.register("/w/toxi", pid=102)
    _as(monkeypatch, 102)
    from toxi import cli as cli_mod
    assert (cli_mod._default_session(), cli_mod._default_session_dir()) == ("toxi-2", "/w/toxi")
    _as(monkeypatch, 999)  # a plain terminal: no session
    assert (cli_mod._default_session(), cli_mod._default_session_dir()) == (None, None)


def _incoming_in(d, body, **meta):
    _incoming(d, body, **meta)  # its bounce, if any, is stored after it
    return d.db.execute("SELECT msg_uuid FROM messages WHERE direction='in' "
                        "ORDER BY id DESC LIMIT 1").fetchone()[0]


def _bounces(d):
    return d.db.execute("SELECT content, reply_to, thread FROM messages "
                        "WHERE direction='out' AND kind='bounce' ORDER BY id").fetchall()


def test_a_text_no_session_can_take_goes_back_to_the_sender(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, '[peers.codex]\ntrust = "collaborator"\n')
    d = _daemon(tmp_path, monkeypatch)
    sessions.register("/w/those-moments", pid=101)
    sessions.register("/w/OnesVibe", pid=102)
    unread = lambda u: d._get_message({"msg_uuid": u})["message"]["read_at"] is None  # noqa: E731

    ok = _incoming_in(d, "@OnesVibe deploy done")
    assert unread(ok) and _bounces(d) == []
    wrong = _incoming_in(d, "@scout-daily batch 201", thread="scout-daily")
    assert not unread(wrong)
    body, reply_to, thread = _bounces(d)[-1]
    assert (reply_to, thread) == (wrong, "scout-daily") and not body.startswith("@")
    assert "'scout-daily'" in body and "OnesVibe, those-moments" in body
    vague = _incoming_in(d, "W9 progress")  # no @name, two sessions, no binding
    assert not unread(vague) and "no @name" in _bounces(d)[-1][0]
    n = len(_bounces(d))
    assert unread(_incoming_in(d, "@nobody", kind="bounce")) and len(_bounces(d)) == n  # no loops
    assert unread(_incoming_in(d, "@nobody", kind="ack")) and len(_bounces(d)) == n

    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "observer"\n')
    _incoming_in(d, "@nobody hi")
    assert "Running sessions" not in _bounces(d)[-1][0]  # observers do not see the list

    LIVE.difference_update({101, 102, 103})  # no session runs: keep it for the next one
    try:
        assert unread(_incoming_in(d, "@nobody hi")) and len(_bounces(d)) == n + 1
    finally:
        LIVE.update({101, 102, 103})


def test_one_process_can_hold_several_sessions_by_their_ids(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    a = sessions.register("/w/app", pid=101, sid="ses_a", name="fix login")
    b = sessions.register("/w/app", pid=101, sid="ses_b")
    assert (a["name"], b["name"]) == ("fix-login", "app")
    assert sessions.current(101, "ses_a")["name"] == "fix-login"
    assert sorted(s["sid"] for s in sessions.live()) == ["ses_a", "ses_b"]
    sessions.end(101, "ses_a")
    assert [s["sid"] for s in sessions.live()] == ["ses_b"]
    # The process lives on, but an entry its adapter stopped renewing ends.
    e = sessions.current(101, "ses_b")
    e["seen"] -= sessions.SID_TTL + 1
    sessions._file(101, "ses_b").write_text(json.dumps(e))
    assert sessions.live() == []


def test_the_session_id_comes_from_the_agents_environment(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    monkeypatch.setenv("PI_SESSION_ID", "pi-123")
    me = sessions.register("/w/x", pid=102)
    assert me["sid"] == "pi-123" and sessions.current(102)["sid"] == "pi-123"


def test_a_helper_without_the_id_joins_the_one_session_of_its_process(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    sessions.register("/w/p", pid=101, sid="pi-1")
    assert sessions.register("/w/p", pid=101)["sid"] == "pi-1"  # Pi's MCP server: no PI_SESSION_ID
    assert len(sessions.live()) == 1
    sessions.register("/w/p", pid=102, sid="oc-1")
    sessions.register("/w/p", pid=102, sid="oc-2")
    me = sessions.register("/w/p", pid=102)  # two sessions in it: cannot tell
    assert "sid" not in me and me["transient"]
    assert len([s for s in sessions.live() if s["pid"] == 102]) == 2  # and no third one appears


def test_a_caller_without_a_name_keeps_the_name_the_adapter_gave(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "")
    assert sessions.register("/w/p", pid=103, sid="ses_1", name="Greeting")["name"] == "Greeting"
    assert sessions.register("/w/p", pid=103, sid="ses_1")["name"] == "Greeting"  # MCP server, failed_sends
    assert sessions.register("/w/p", pid=103, sid="ses_1", name="Review")["name"] == "Review"  # a rename


def test_a_codex_thread_id_is_the_session_id_only_in_the_shared_daemon(monkeypatch):
    import types
    cmds = {1: "/x/codex app-server --remote-control --listen unix://",  # the shared daemon
            2: "/Applications/ChatGPT.app/.../codex -c a=b app-server --analytics-default-enabled",  # the app's own
            3: "codex -C /w/p"}  # a TUI with --no-daemon
    monkeypatch.setattr(sessions.subprocess, "run",
                        lambda args, **kw: types.SimpleNamespace(stdout=cmds[int(args[-1])] + "\n"))
    assert sessions.codex_sid("t1", pid=1) is None  # the hooks did not register it by thread: old hooks
    monkeypatch.setattr(sessions, "live", lambda: [{"pid": 1, "sid": "t1", "name": "p"}])
    assert sessions.codex_sid("t1", pid=1) == "t1"
    assert sessions.codex_sid("t1", pid=2) is None
    assert sessions.codex_sid("t1", pid=3) is None
    assert sessions.codex_sid(None, pid=1) is None


# --- the session directory on the card (design §13.6, C8) ---

def _cards(d):
    from toxi import envelope
    return [e["data"] for _, e in d.tox.sent if e["type"] == "profile"]


def test_collaborators_get_the_session_directory_and_observers_do_not(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    _setup(tmp_path, monkeypatch, '[peers.codex]\ntrust = "collaborator"\n')
    monkeypatch.setenv("TOXI_SESSION_ABOUT", "iOS build")
    sessions.register(str(tmp_path / "ios"), pid=101)
    monkeypatch.delenv("TOXI_SESSION_ABOUT")
    sessions.register(str(tmp_path / "web"), pid=102)
    fn = d.db.execute("SELECT friend_number FROM contacts WHERE alias='codex'").fetchone()[0]
    d._send_profile(fn)
    assert _cards(d)[-1]["sessions"] == [{"name": "ios", "about": "iOS build"}, {"name": "web"}]
    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "observer"\n')
    d.profile = {"kind": "agent"}
    d._send_profile(fn)
    assert "sessions" not in _cards(d)[-1]  # a session name can tell a project's name


def test_the_directory_goes_out_again_only_when_it_changes(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    _setup(tmp_path, monkeypatch, '[peers.codex]\ntrust = "owner"\n')
    sessions.register(str(tmp_path / "ios"), pid=101)
    d._directory_tick()
    d._directory_tick()
    assert len(_cards(d)) == 1
    sessions.register(str(tmp_path / "web"), pid=102)
    d._directory_tick()
    assert [s["name"] for s in _cards(d)[-1]["sessions"]] == ["ios", "web"]


def test_a_long_directory_fits_one_tox_message(tmp_path, monkeypatch):
    from toxi import envelope
    d = _daemon(tmp_path, monkeypatch)
    _setup(tmp_path, monkeypatch, '[peers.codex]\ntrust = "collaborator"\n')
    monkeypatch.setattr(sessions, "_alive", lambda pid: True)
    monkeypatch.setenv("TOXI_SESSION_ABOUT", "x" * 120)
    for i in range(16):
        sessions.register(str(tmp_path / f"project-with-a-long-name-{i:02d}-" / ("y" * 30)), pid=200 + i)
    fn = d.db.execute("SELECT friend_number FROM contacts WHERE alias='codex'").fetchone()[0]
    d._send_profile(fn)
    card = _cards(d)[-1]
    assert len(envelope.encode(envelope.make_profile(card))) <= 1372
    assert card["sessions"] and not any("about" in s for s in card["sessions"])  # the purposes go first


def test_a_card_keeps_a_bounded_directory_and_a_session_sets_its_purpose(tmp_path, monkeypatch):
    from toxi import envelope
    card = envelope.clean_profile({"kind": "agent", "sessions": [{"name": "a" * 99, "about": "b" * 200},
                                                                  {"about": "no name"}, "junk"] * 9})
    assert len(card["sessions"]) <= 16 and len(card["sessions"][0]["name"]) == 64
    assert len(card["sessions"][0]["about"]) == 120
    _setup(tmp_path, monkeypatch, "")
    _as(monkeypatch, 101)
    sessions.register(str(tmp_path / "ios"), pid=101)
    assert CliRunner().invoke(cli, ["about", "fixing the iOS build"]).exit_code == 0
    assert sessions.register(str(tmp_path / "ios"), pid=101)["about"] == "fixing the iOS build"
    _as(monkeypatch, 999)  # not an agent session
    assert CliRunner().invoke(cli, ["about", "x"]).exit_code != 0
