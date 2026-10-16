"""CLI surface for identity & pairing: peers, profile, pair, join, send --session."""
import json
import time

from click.testing import CliRunner

from toxi import invite
from toxi.cli import cli


def _fake(monkeypatch, responses):
    calls = []

    def request(method, params=None, **kw):
        calls.append((method, params or {}))
        return responses[method]

    monkeypatch.setattr("toxi.client.request", request)
    return calls


def test_pair_prints_invite_and_join_hint(monkeypatch):
    _fake(monkeypatch, {"create_invite": {"invite": "ABC", "expires_at": 1, "name": "ada"}})
    out = CliRunner().invoke(cli, ["pair", "--ttl", "120"]).output
    assert "valid 2 min" in out and "toxi join ABC" in out


def test_join_adds_contact_with_nonce_request(monkeypatch):
    calls = _fake(monkeypatch, {"get_profile": {"profile": {"name": "codex@Ada"}},
                                "add_contact": {"alias": "ada"}})
    text = invite.encode({"v": 1, "tox_id": "A" * 76, "name": "ada", "nonce": "n1",
                          "exp": int(time.time()) + 60})
    r = CliRunner().invoke(cli, ["join", text])
    assert r.exit_code == 0, r.output
    method, params = calls[-1]
    assert method == "add_contact" and params["alias"] == "ada" and params["tox_id"] == "A" * 76
    assert invite.parse_request(params["request_msg"].encode()) == ("n1", "codex@Ada")


def test_join_rejects_expired_invite(monkeypatch):
    _fake(monkeypatch, {})
    text = invite.encode({"v": 1, "tox_id": "A" * 76, "name": "ada", "nonce": "n1", "exp": 1})
    r = CliRunner().invoke(cli, ["join", text])
    assert r.exit_code != 0 and "expired" in r.output


def test_peers_renders_cards(monkeypatch):
    monkeypatch.setattr("toxi.peers.overview", lambda: [
        {"alias": "castle-op", "peer": True, "trust": "observer", "dirs": ["/w/chess"],
         "known": True, "online": True, "last_seen": 1, "unread": 2,
         "profile": {"kind": "agent", "runtime": "grok", "host": "vm1", "operator": "jeff"}},
        {"alias": "newbie", "peer": True, "trust": "observer", "dirs": [], "known": False,
         "online": False, "last_seen": None, "unread": 0, "profile": None},
    ])
    out = CliRunner().invoke(cli, ["peers"]).output
    assert "✓ castle-op    observer     agent/grok @ vm1 · operator jeff · 📬 2" in out
    assert "delivered only to sessions started in: /w/chess" in out
    assert "newbie" in out and "(no profile card yet) (not a contact yet)" in out


def test_profile_set_fills_host_and_show_lists_fields(monkeypatch):
    calls = _fake(monkeypatch, {
        "get_profile": {"profile": {}},
        "set_profile": {"profile": {"kind": "agent"}, "sent_to": 3},
    })
    r = CliRunner().invoke(cli, ["profile", "set", "--kind", "agent", "--runtime", "codex"])
    assert "sent to 3 online contact(s)" in r.output
    sent = calls[-1][1]["profile"]
    assert sent["kind"] == "agent" and sent["runtime"] == "codex" and sent["host"]

    _fake(monkeypatch, {"get_profile": {"profile": {"kind": "agent", "caps": ["profile", "session"]}}})
    out = CliRunner().invoke(cli, ["profile", "show"]).output
    assert "kind      agent" in out and "caps      profile, session" in out
    assert CliRunner().invoke(cli, ["--json", "profile", "show"]).output.strip() == \
        json.dumps({"kind": "agent", "caps": ["profile", "session"]})


def test_send_tags_session_from_env(monkeypatch):
    calls = _fake(monkeypatch, {"send_message": {"msg_uuid": "u", "status": "sent"}})
    monkeypatch.delenv("TOXI_SESSION", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/w/OnesVibe")
    CliRunner().invoke(cli, ["send", "bob", "hi"])
    assert calls[-1][1] == {"alias": "bob", "body": "hi", "session": "OnesVibe"}
    CliRunner().invoke(cli, ["send", "bob", "hi", "--session", "ops"])
    assert calls[-1][1]["session"] == "ops"
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    CliRunner().invoke(cli, ["send", "bob", "hi"])
    assert calls[-1][1]["session"] is None  # the CLI never guesses from the cwd


def test_unread_shows_sender_session(monkeypatch):
    _fake(monkeypatch, {
        "get_messages": {"messages": [{"msg_uuid": "1", "alias": "ada", "msg_type": "text",
                                       "content": "hi", "created_at": int(time.time()),
                                       "from_session": "powerup-chess", "direction": "in"}]},
        "mark_read": {"marked": 1},
    })
    assert "ada@powerup-chess" in CliRunner().invoke(cli, ["unread"]).output
