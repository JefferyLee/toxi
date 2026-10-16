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
    assert mcp_server.send_message("bob", "hi")["status"] == "queued"
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
