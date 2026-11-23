"""Design §13.12: messages between two sessions of one identity stay on this
machine. Stored as sent (`id`) and as received (`id:in`), routed by `@name`,
receipted at once, with task states; the sessions are collaborators."""
import pytest

from toxi import config, peers, sessions
from toxi.daemon import RpcError

from test_hardening import _wire  # noqa: E402  (client calls go straight to the daemon)
from test_routing_done import _daemon  # noqa: E402  (contact `codex`, friend 5)


def _two_sessions(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    _wire(d, monkeypatch)
    (tmp_path / "config.toml").write_text("")
    monkeypatch.delenv("TOXI_SESSION", raising=False)
    monkeypatch.setattr(sessions, "_alive", lambda pid: pid in (101, 102))
    ios = sessions.register(str(tmp_path / "ios"), pid=101, name="SF-iOS")
    fish = sessions.register(str(tmp_path / "fish"), pid=102, name="SwordFish")
    return d, ios, fish


def _send(d, me, body, alias="self", **kw):
    return d._send_message(dict(alias=alias, body=body, session=me["name"], session_dir=me["cwd"], **kw))


def _row(d, uid):
    return d.db.execute("SELECT * FROM messages WHERE msg_uuid=?", (uid,)).fetchone()


def _inbox(monkeypatch, me, take=False):
    """What `me` gets, asking as that session (its agent process)."""
    monkeypatch.setattr(sessions, "agent_pid", lambda: me["pid"])
    return (peers.take if take else peers.pending)(None, me["cwd"])


def _for(monkeypatch, me):
    return [m["content"] for m in _inbox(monkeypatch, me)]


def test_a_message_to_another_session_stays_here_and_reaches_only_it(tmp_path, monkeypatch):
    d, ios, fish = _two_sessions(tmp_path, monkeypatch)
    res = _send(d, ios, "@SwordFish the build is green")
    assert res["status"] == "delivered" and res["local"] and not d.tox.sent  # no Tox
    assert _row(d, res["msg_uuid"] + ":in")["from_session"] == "SF-iOS"
    assert _for(monkeypatch, fish) == ["@SwordFish the build is green"] and _for(monkeypatch, ios) == []
    got = _inbox(monkeypatch, fish)[0]
    assert got["alias"] == config.SELF and got["trust"] == "collaborator"  # never owner
    _send(d, ios, "and the tests too", alias="SwordFish@self")  # the label form, no @ in the body
    assert _for(monkeypatch, fish)[-1] == "@swordfish and the tests too"


def test_a_local_message_needs_another_running_session(tmp_path, monkeypatch):
    d, ios, fish = _two_sessions(tmp_path, monkeypatch)
    for body in ("no target", "@nobody hi", "@SF-iOS talking to myself"):
        with pytest.raises(RpcError) as e:
            _send(d, ios, body)
        assert "SwordFish" in str(e.value)  # it names the sessions there are


def test_an_answer_goes_back_to_the_sender_and_receipts_at_once(tmp_path, monkeypatch):
    d, ios, fish = _two_sessions(tmp_path, monkeypatch)
    q = _send(d, ios, "@SwordFish which branch?", kind="request")["msg_uuid"]
    got = _inbox(monkeypatch, fish, take=True)[0]
    assert _row(d, q)["status"] == "read"  # the read receipt, without Tox
    _send(d, fish, "@SF-iOS main", reply_to=got["msg_uuid"])
    assert _row(d, q)["status"] == "handled"  # answered: done
    assert _for(monkeypatch, ios) == ["@SF-iOS main"]
    answer = _inbox(monkeypatch, ios)[0]
    assert answer["reply_to"] == q and answer["route_session"] == "SF-iOS"


def test_task_states_between_two_sessions(tmp_path, monkeypatch):
    d, ios, fish = _two_sessions(tmp_path, monkeypatch)
    q = _send(d, ios, "@SwordFish ship it", kind="request")["msg_uuid"]
    theirs = q + ":in"
    _send(d, fish, "@SF-iOS which version?", reply_to=theirs, state="input-required")
    assert _row(d, q)["task_state"] == _row(d, theirs)["task_state"] == "input-required"
    _send(d, ios, "@SwordFish never mind", reply_to=q, state="canceled")
    assert _row(d, q)["task_state"] == _row(d, theirs)["task_state"] == "canceled"
    with pytest.raises(RpcError):  # ended stays ended
        _send(d, fish, "@SF-iOS shipped", reply_to=theirs, state="completed")


def test_self_is_not_a_configured_peer():
    assert config.SELF not in config.peer_config() and config.SELF in config.routing_peers()
