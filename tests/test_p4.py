"""P4: webhook with HMAC, toxi rename, toxi agent run, toxi doctor, setup headless."""
import hashlib
import hmac
import json
import selectors
import sys
from collections import deque

import pytest
from click.testing import CliRunner

from toxi import daemon as daemon_mod
from toxi import db, envelope, service
from toxi.cli import cli
from toxi.daemon import Daemon, RpcError

from test_events import FakeTox  # noqa: E402


def _daemon(tmp_path, monkeypatch, config_text=""):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(config_text)
    d = object.__new__(Daemon)
    d.db = db.connect(tmp_path / "chat.db")
    d.tox = FakeTox()
    d.profile = {}
    d._text_times, d._request_times = {}, deque()
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d._subs, d._parts, d._sel = [], {}, selectors.DefaultSelector()
    d._dropped_parts = deque(maxlen=200)
    d._transfers, d._out, d._file_meta = {}, {}, {}
    d.db.execute("INSERT INTO contacts (public_key, alias, added_at, friend_number, is_online) "
                 "VALUES ('CC', 'codex', 1, 5, 1)")
    d.db.commit()
    return d


class SyncThread:  # runs the webhook poster inline so the test can see it
    def __init__(self, target, args=(), daemon=True):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_webhook_posts_signed_batch_with_optional_bodies(tmp_path, monkeypatch):
    posted = []
    monkeypatch.setattr(daemon_mod.threading, "Thread", SyncThread)
    monkeypatch.setattr(daemon_mod, "_post_json", lambda url, body, headers: posted.append((url, body, headers)))
    d = _daemon(tmp_path, monkeypatch, '[hooks]\non_message_url = "https://x.test/wake"\n'
                                       'on_message_secret = "s3cret"\ninclude_body = true\n')
    d._on_friend_message(5, envelope.encode(envelope.make_text("hi there", "s1", meta={"thread": "t"})), 0)
    d._hook_due = 0
    d._tick_hook()
    (url, body, headers), = posted
    assert url == "https://x.test/wake"
    data = json.loads(body)
    assert data["from"] == ["codex"] and data["count"] == 1
    m = data["messages"][0]
    assert m["content"] == "hi there" and m["thread"] == "t" and m["from_session"] == "s1"
    ts = headers["X-Toxi-Timestamp"]
    expect = hmac.new(b"s3cret", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert headers["X-Toxi-Signature"] == f"sha256={expect}"

    posted.clear()
    d2 = _daemon(tmp_path / "nobody", monkeypatch, '[hooks]\non_message_url = "https://x.test/wake"\n')
    d2._on_friend_message(5, envelope.encode(envelope.make_text("secret body")), 0)
    d2._hook_due = 0
    d2._tick_hook()
    (_, body, headers), = posted
    assert "secret body" not in body.decode() and "X-Toxi-Signature" not in headers
    assert json.loads(body)["messages"][0]["msg_type"] == "text"


def test_rename_contact(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    assert d._rename_contact({"alias": "codex", "new_alias": "codex@Ada"}) == {"alias": "codex@Ada", "was": "codex"}
    with pytest.raises(RpcError):
        d._rename_contact({"alias": "nope", "new_alias": "x"})
    d.db.execute("INSERT INTO contacts (public_key, alias, added_at) VALUES ('DD', 'bob', 1)")
    d.db.commit()
    with pytest.raises(RpcError) as e:
        d._rename_contact({"alias": "bob", "new_alias": "codex@Ada"})
    assert e.value.code == "CONTACT_EXISTS"


def test_cli_rename_warns_about_peer_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "collaborator"\n')
    monkeypatch.setattr("toxi.client.request", lambda m, p=None, **k: {"alias": p["new_alias"], "was": p["alias"]})
    out = CliRunner().invoke(cli, ["rename", "codex", "codex2"]).output
    assert "codex is now codex2" in out and "[peers.codex2]" in out


def test_agent_run_delivers_and_replies_in_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[peers.codex]\ntrust = "observer"\n')
    msg = {"msg_uuid": "u1", "alias": "codex", "msg_type": "text", "content": "status?",
           "thread": "ops", "kind": "request", "from_session": "tm", "profile": None}
    takes, sent = [], []

    def take(alias=None, cwd=None, thread=None):
        takes.append(alias)
        return [dict(msg, trust="observer")] if alias == "codex" else []

    def subscribe(aliases=None, context=0, **k):
        assert aliases is None and context == 2
        yield {"event": "contact.online", "data": {"alias": "codex"}}
        yield {"event": "message.new", "data": dict(msg, direction="in", context=[{"content": "earlier"}])}
        yield {"event": "message.new", "data": {"direction": "in", "alias": "stranger"}}  # not a peer
        raise KeyboardInterrupt

    monkeypatch.setattr("toxi.peers.take", take)
    monkeypatch.setattr("toxi.client.subscribe", subscribe)
    monkeypatch.setattr("toxi.client.request", lambda m, p=None, **k: sent.append((m, p)) or {"msg_uuid": "r", "status": "sent"})
    cmd = (f"{sys.executable} -c \"import json,sys; p=json.load(sys.stdin); "
           f"print('echo:', p['message']['content'], p['trust'], len(p['context']), 'guide' in p)\"")
    r = CliRunner().invoke(cli, ["agent", "run", "--exec", cmd, "--context", "2", "--session", "bot"])
    assert r.exit_code == 0, r.output
    assert takes == [None, "codex"]  # drain once, then the routed event; the stranger is skipped
    (method, params), = sent
    assert method == "send_message" and params["body"] == "echo: status? observer 1 True"
    assert params["thread"] == "ops" and params["reply_to"] == "u1" and params["kind"] == "report"
    assert params["session"] == "bot" and "replied" in r.output


def test_doctor_reports_problems_and_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[peers.codex]\ndirs = ["/no/such/dir"]\ntrust = "observer"\n')
    monkeypatch.setattr("toxi.bootstrap.daemon_running", lambda: False)
    monkeypatch.setattr(service, "status", lambda: {"installed": False, "running": False, "pid": None, "path": "p"})
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = CliRunner().invoke(cli, ["doctor"])
    assert r.exit_code == 1
    assert "⚠ identity" in r.output and "⚠ daemon" in r.output and "⚠ peer codex" in r.output
    assert "/no/such/dir" in r.output and "problem(s)" in r.output
    data = json.loads(CliRunner().invoke(cli, ["--json", "doctor"]).output)
    assert {c["check"] for c in data} >= {"engine", "libtoxcore", "identity", "daemon", "service", "peer codex"}


def test_setup_headless_sets_card_and_falls_back_to_cron(monkeypatch):
    calls = []
    monkeypatch.setattr("toxi.cli._setup_engine", lambda: calls.append("engine"))
    monkeypatch.setattr("toxi.client.request", lambda m, p=None, **k: calls.append((m, p)) or
                        {"profile": {}, "sent_to": 0, "tox_id": "A" * 76, "name": "x", "connection": 0})

    def no_manager():
        raise service.ServiceError("systemctl not found (no systemd user session on this machine)")

    monkeypatch.setattr(service, "install", no_manager)
    r = CliRunner().invoke(cli, ["setup", "headless", "--name", "castle-op", "--runtime", "grok",
                                 "--operator", "jeff"])
    assert r.exit_code == 0, r.output
    prof = next(p for m, p in calls[1:] if m == "set_profile")["profile"]
    assert prof["kind"] == "agent" and prof["runtime"] == "grok" and prof["name"] == "castle-op"
    assert "crontab" in r.output and "Tox ID: " + "A" * 76 in r.output and "toxi agent run" in r.output
