"""CLI surface added in P2: show, watch, send --wait-read, read --oldest-first, service."""
import json
import time

from click.testing import CliRunner

from toxi import ipc, service
from toxi.cli import cli


def _fake(monkeypatch, handler):
    monkeypatch.setattr("toxi.client.request", lambda method, params=None, **kw: handler(method, params or {}))


def test_show_prints_the_full_message(monkeypatch):
    msg = {"msg_uuid": "u1", "alias": "vibe-op", "direction": "in", "msg_type": "text",
           "content": "x" * 3000, "created_at": int(time.time()), "status": "read",
           "from_session": "ops"}
    _fake(monkeypatch, lambda m, p: {"message": msg if p.get("msg_uuid") == "u1" else None})
    out = CliRunner().invoke(cli, ["show", "u1"]).output
    assert out.startswith("vibe-op@ops ·") and "x" * 3000 in out
    r = CliRunner().invoke(cli, ["show", "nope"])
    assert r.exit_code != 0 and "no message with id" in r.output


def test_watch_streams_events(monkeypatch):
    evs = [{"event": "message.new", "ts": 0, "data": {"alias": "bob", "from_session": "s",
                                                      "msg_type": "text", "content": "hi"}},
           {"event": "contact.offline", "ts": 0, "data": {"alias": "bob"}},
           {"event": "receipt", "ts": 0, "data": {"alias": "bob", "msg_uuid": "u9", "kind": "read"}}]

    def fake_sub(aliases=None, context=0, **kw):
        assert list(aliases) == ["bob"] and context == 3
        yield from evs
        raise ipc.IPCError("closed")

    monkeypatch.setattr("toxi.client.subscribe", fake_sub)
    out = CliRunner().invoke(cli, ["watch", "--alias", "bob", "--context", "3"]).output
    assert "message   bob@s: hi" in out and "offline   bob" in out and "receipt   bob u9" in out
    out = CliRunner().invoke(cli, ["--json", "watch", "--alias", "bob", "--context", "3"]).output
    assert [json.loads(l)["event"] for l in out.splitlines()] == ["message.new", "contact.offline", "receipt"]


def test_send_wait_read_reports_the_receipt(monkeypatch):
    seen = {"get": 0}

    def handler(method, params):
        if method == "send_message":
            return {"msg_uuid": "u", "status": "sent"}
        seen["get"] += 1
        return {"message": {"status": "read" if seen["get"] >= 2 else "delivered"}}

    _fake(monkeypatch, handler)
    out = CliRunner().invoke(cli, ["send", "bob", "hi", "--wait-read", "5"]).output
    assert "✓ read by bob" in out
    data = json.loads(CliRunner().invoke(cli, ["--json", "send", "bob", "hi", "--wait-read", "5"]).output)
    assert data["read"] is True and data["status"] == "read"


def test_read_oldest_first_applies_to_json(monkeypatch):
    msgs = [{"msg_uuid": "2", "alias": "bob", "direction": "in", "msg_type": "text", "content": "two",
             "created_at": 2, "status": "read", "read_at": 1},
            {"msg_uuid": "1", "alias": "bob", "direction": "in", "msg_type": "text", "content": "one",
             "created_at": 1, "status": "read", "read_at": 1}]
    _fake(monkeypatch, lambda m, p: {"messages": msgs} if m == "get_messages" else {"marked": 0})
    newest = json.loads(CliRunner().invoke(cli, ["--json", "read", "bob"]).output)
    oldest = json.loads(CliRunner().invoke(cli, ["--json", "read", "bob", "--oldest-first"]).output)
    assert [m["content"] for m in newest] == ["two", "one"]
    assert [m["content"] for m in oldest] == ["one", "two"]
    human = CliRunner().invoke(cli, ["read", "bob", "--oldest-first"]).output
    assert human.index("one") < human.index("two")


def test_service_commands(monkeypatch, tmp_path):
    state = {"installed": False, "running": False}
    monkeypatch.setattr("toxi.bootstrap.daemon_running", lambda: state["running"])

    def install():
        state.update(installed=True, running=True)
        return tmp_path / "dev.toxi.daemon.plist"

    monkeypatch.setattr(service, "install", install)
    monkeypatch.setattr(service, "installed", lambda: state["installed"])
    monkeypatch.setattr(service, "status", lambda: {"installed": state["installed"], "running": state["running"],
                                                    "pid": 77 if state["running"] else None, "path": "p"})
    def uninstall():
        was = state["installed"]
        state.update(installed=False, running=False)
        return was

    monkeypatch.setattr(service, "uninstall", uninstall)
    monkeypatch.setattr(service, "restart", lambda: None)
    r = CliRunner()
    assert "not installed" in r.invoke(cli, ["service", "status"]).output
    assert r.invoke(cli, ["service", "restart"]).exit_code != 0
    out = r.invoke(cli, ["service", "install"]).output
    assert "service installed" in out and "daemon is up" in out
    assert "running (pid 77)" in r.invoke(cli, ["service", "status"]).output
    assert "✓ daemon restarted" in r.invoke(cli, ["service", "restart"]).output
    assert "service removed" in r.invoke(cli, ["service", "uninstall"]).output
    assert "no service installed" in r.invoke(cli, ["service", "uninstall"]).output


def test_daemon_running_treats_half_closed_socket_as_down(monkeypatch):
    from toxi import bootstrap, ipc

    def half_closed(method, params=None, **kw):
        raise ipc.IPCError("connection closed before frame was complete")

    monkeypatch.setattr("toxi.client.request", half_closed)
    assert bootstrap.daemon_running() is False
