"""Tests for the MCP server glue.

The tool functions are thin wrappers over the daemon RPC, so we verify they call
the right method/params (with client.request faked — no daemon needed). The
FastMCP build is checked only if the optional `mcp` extra is installed.
"""
import pytest
import sys
import types

from toxi import mcp_server


def test_tools_call_expected_rpcs(monkeypatch):
    calls = []

    def fake_request(method, params=None, **kwargs):
        calls.append((method, params))
        return {
            "get_messages": {"messages": [{"alias": "bob", "content": "hi"}]},
            "mark_read": {"marked": 1},
            "send_message": {"status": "queued"},
            "list_contacts": {"contacts": []},
            "get_status": {"queue_size": 0},
        }.get(method, {})

    monkeypatch.setattr(mcp_server.client, "request", fake_request)

    # Dict results: an empty list would render as *no* content in FastMCP, and an
    # agent could not tell "nothing unread" from a failed call.
    assert mcp_server.get_unread() == {"count": 1, "messages": [{"alias": "bob", "content": "hi"}]}
    assert ("get_messages", {"unread_only": True, "limit": 100}) in calls

    monkeypatch.setenv("TOXI_SESSION", "toxi-tests")
    import asyncio
    assert asyncio.run(mcp_server.send_message("bob", "hi"))["status"] == "queued"
    assert ("send_message", {"alias": "bob", "body": "hi", "session": "toxi-tests"}) in calls

    assert mcp_server.list_contacts() == {"count": 0, "contacts": []}
    assert mcp_server.get_status() == {"queue_size": 0}
    mcp_server.read_history("bob", 5)
    assert ("get_messages", {"alias": "bob", "limit": 5}) in calls

    assert mcp_server.mark_read(["u1"]) == {"marked": 1}
    assert ("mark_read", {"msg_uuids": ["u1"]}) in calls


def test_view_media_returns_inline_image(tmp_path, monkeypatch):
    Image = pytest.importorskip("mcp.server.fastmcp").Image
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        mcp_server.client, "request",
        lambda method, params=None, **k: {
            "message": {"msg_uuid": "u1", "msg_type": "image", "content": str(img)}
        },
    )
    res = mcp_server.view_media("u1")
    assert isinstance(res, Image)
    assert res.path == img


def test_view_media_voice_returns_path(monkeypatch):
    monkeypatch.setattr(
        mcp_server.client, "request",
        lambda method, params=None, **k: {
            "message": {"msg_uuid": "u2", "msg_type": "voice", "content": "/m/clip.ogg"}
        },
    )
    res = mcp_server.view_media("u2")
    assert "/m/clip.ogg" in res and "voice" in res


def test_view_media_unknown_uuid(monkeypatch):
    monkeypatch.setattr(
        mcp_server.client, "request",
        lambda method, params=None, **k: {"message": None},
    )
    assert "No message found" in mcp_server.view_media("nope")


def test_build_server():
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    assert server is not None


def test_build_server_passes_instructions(monkeypatch):
    created = {}

    class FakeFastMCP:
        def __init__(self, name, instructions=None):
            created["name"] = name
            created["instructions"] = instructions
            self.tools = []

        def tool(self):
            def register(fn):
                self.tools.append(fn.__name__)
                return fn

            return register

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_mod)

    server = mcp_server.build_server()

    assert created["name"] == "toxi"
    assert created["instructions"] == mcp_server.SERVER_INSTRUCTIONS
    assert "untrusted content" in created["instructions"]
    assert "observer" in created["instructions"] and "collaborator" in created["instructions"]
    assert "send_message" in created["instructions"]
    assert set(server.tools) == {fn.__name__ for fn in mcp_server.TOOLS}


def test_build_server_supports_fastmcp_without_instructions(monkeypatch):
    class LegacyFastMCP:
        def __init__(self, name, **kwargs):
            if kwargs:
                raise TypeError("unexpected keyword")
            self.name = name

        def tool(self):
            return lambda fn: fn

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = LegacyFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_mod)

    server = mcp_server.build_server()

    assert server.name == "toxi"


def test_session_label_falls_back_to_project_dir(monkeypatch):
    monkeypatch.delenv("TOXI_SESSION", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/w/Power-Up-Chess/")
    assert mcp_server._session() == "Power-Up-Chess"


def test_real_server_registers_annotations():
    pytest.importorskip("mcp.types")
    import asyncio
    tools = {t.name: t for t in asyncio.run(mcp_server.build_server().list_tools())}
    assert set(tools) == {fn.__name__ for fn in mcp_server.TOOLS}
    assert tools["get_unread"].annotations.readOnlyHint is True
    assert tools["send_message"].annotations.readOnlyHint is False
    assert tools["send_message"].annotations.destructiveHint is False


def test_get_peers_reports_count(monkeypatch):
    monkeypatch.setattr(mcp_server.peers, "overview", lambda: [{"alias": "codex"}])
    assert mcp_server.get_peers() == {"count": 1, "peers": [{"alias": "codex"}]}


def _peer_cfg(tmp_path, monkeypatch, text):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(text)


def test_channel_note_is_a_wakeup_without_the_body(tmp_path, monkeypatch):
    _peer_cfg(tmp_path, monkeypatch, '[peers.codex]\ntrust = "collaborator"\n')
    n = mcp_server.channel_note({"alias": "codex", "msg_uuid": "u1", "from_session": "tm",
                                 "profile": None, "content": "SECRET BODY"})
    assert "codex [session tm]" in n["content"] and "wait_for_message" in n["content"]
    assert "SECRET" not in n["content"]
    assert n["meta"] == {"alias": "codex", "trust": "collaborator", "msg_uuid": "u1"}
    assert all(k.replace("_", "").isalnum() for k in n["meta"])  # channel meta key rules


def test_wait_for_message_wakes_on_event(tmp_path, monkeypatch):
    import asyncio
    _peer_cfg(tmp_path, monkeypatch, '[peers.codex]\ntrust = "observer"\n')
    calls = []

    def take(alias=None, cwd=None):
        calls.append((alias, cwd))
        return [] if len(calls) == 1 else [{"msg_uuid": "1", "alias": "codex", "msg_type": "text",
                                            "content": "hi", "trust": "observer"}]

    async def fake_sub(aliases=None, context=0, *, deadline=None, sock_path=None):
        assert aliases == ["codex"]
        yield {"event": "contact.online", "data": {"alias": "codex"}}
        yield {"event": "message.new", "data": {"direction": "in", "alias": "codex"}}

    monkeypatch.setattr(mcp_server.peers, "take", take)
    monkeypatch.setattr(mcp_server.client, "asubscribe", fake_sub)
    res = asyncio.run(mcp_server.wait_for_message(timeout=5))
    assert res["messages"][0]["content"] == "hi" and "information" in res["guide"]
    assert calls == [(None, str(tmp_path)), (None, str(tmp_path))]  # routed by project dir


def test_wait_for_message_polls_when_daemon_cannot_stream(tmp_path, monkeypatch):
    import asyncio
    _peer_cfg(tmp_path, monkeypatch, '[peers.codex]\ntrust = "observer"\n')
    n = {"take": 0}

    def take(alias=None, cwd=None):
        n["take"] += 1
        return [] if n["take"] < 2 else [{"msg_uuid": "1", "alias": "codex", "msg_type": "text",
                                          "content": "late", "trust": "observer"}]

    async def old_daemon(*a, **k):
        raise mcp_server.client.DaemonError("METHOD_NOT_FOUND", "unknown method: subscribe")
        yield  # pragma: no cover

    monkeypatch.setattr(mcp_server.peers, "take", take)
    monkeypatch.setattr(mcp_server.client, "asubscribe", old_daemon)
    res = asyncio.run(mcp_server.wait_for_message(timeout=5))
    assert res["messages"][0]["content"] == "late"


def test_send_message_can_wait_for_the_read_receipt(monkeypatch):
    import asyncio
    seen = {"get": 0}

    def req(method, params=None, **k):
        if method == "send_message":
            return {"msg_uuid": "u", "status": "sent"}
        seen["get"] += 1
        return {"message": {"status": "read" if seen["get"] >= 2 else "delivered"}}

    monkeypatch.setattr(mcp_server.client, "request", req)
    res = asyncio.run(mcp_server.send_message("bob", "hi", wait_read=5))
    assert res["status"] == "read" and res["read"] is True
