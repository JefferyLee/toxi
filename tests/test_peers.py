"""Peer-agent delivery: config, take(), wait_for_message, and both Stop hooks."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from toxi import mcp_server, peers

ROOT = Path(__file__).resolve().parents[1]


def _fake_daemon(monkeypatch, msgs):
    """Fake client.request backed by a list of unread messages (newest first)."""
    marked = []

    def request(method, params=None, **kw):
        if method == "get_messages":
            return {"messages": [m for m in msgs if m["msg_uuid"] not in marked]}
        if method == "mark_read":
            marked.extend(params["msg_uuids"])
            return {"marked": len(params["msg_uuids"])}
        raise AssertionError(method)

    monkeypatch.setattr(peers.client, "request", request)
    return marked


def _peers_config(tmp_path, monkeypatch, peer_list):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(f"[agents]\npeers = {json.dumps(peer_list)}\n")


MSGS = [
    {"msg_uuid": "3", "alias": "codex", "msg_type": "text", "content": "second"},
    {"msg_uuid": "2", "alias": "bob", "msg_type": "text", "content": "human"},
    {"msg_uuid": "1", "alias": "codex", "msg_type": "text", "content": "first"},
]


def test_take_only_peers_oldest_first_and_marks_read(tmp_path, monkeypatch):
    _peers_config(tmp_path, monkeypatch, ["codex"])
    marked = _fake_daemon(monkeypatch, MSGS)
    got = peers.take()
    assert [m["content"] for m in got] == ["first", "second"]
    assert sorted(marked) == ["1", "3"]  # bob's message stays unread
    assert peers.take() == []


def test_take_without_peers_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    marked = _fake_daemon(monkeypatch, MSGS)
    assert peers.take() == [] and marked == []


def test_as_prompt_lists_messages_and_guide():
    text = peers.as_prompt([{"alias": "codex", "msg_type": "text", "content": "hi"},
                            {"alias": "codex", "msg_type": "image", "content": "/p.png"}])
    assert "- codex: hi" in text and "- codex: [image] /p.png" in text
    assert peers.GUIDE in text


def test_wait_for_message_returns_peer_messages(tmp_path, monkeypatch):
    _peers_config(tmp_path, monkeypatch, ["codex"])
    _fake_daemon(monkeypatch, MSGS)
    res = asyncio.run(mcp_server.wait_for_message(timeout=5))
    assert [m["content"] for m in res["messages"]] == ["first", "second"]


def test_wait_for_message_times_out(tmp_path, monkeypatch):
    _peers_config(tmp_path, monkeypatch, ["codex"])
    _fake_daemon(monkeypatch, [])
    assert asyncio.run(mcp_server.wait_for_message(timeout=0)) == {
        "messages": [], "timed_out": True}


def _fake_toxi(tmp_path, peer_out: str) -> str:
    """Fake `toxi`: answers peer-inbox, records the TOXI_HOME it ran with."""
    p = tmp_path / "faketoxi"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"open({str(tmp_path / 'home_seen')!r}, 'w').write(os.environ.get('TOXI_HOME', ''))\n"
        "args = sys.argv[1:]\n"
        f"if args == ['peer-inbox']: print({peer_out!r})\n"
        "elif args == ['statusline']: print('toxi: 1/1 online')\n"
        "elif args == ['--json', 'unread']: print('[]')\n"
    )
    p.chmod(0o755)
    return str(p)


def _run(hook: Path, env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "TOXI_HOME"}
    env.update(env_extra)
    return subprocess.run([sys.executable, str(hook)], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL)


CC_HOOK = ROOT / "claude-code-plugin" / "hooks" / "stop_hook.py"
CODEX_HOOK = ROOT / "plugins" / "toxi" / "hooks" / "stop_hook.py"


def test_claude_stop_hook_blocks_on_peer_messages(tmp_path):
    r = _run(CC_HOOK, {"TOXI_BIN": _fake_toxi(tmp_path, "[toxi] 1 new message")})
    assert json.loads(r.stdout) == {"decision": "block", "reason": "[toxi] 1 new message"}


def test_claude_stop_hook_silent_without_peer_messages(tmp_path):
    r = _run(CC_HOOK, {"TOXI_BIN": _fake_toxi(tmp_path, "")})
    assert r.returncode == 0 and r.stdout == ""


def test_codex_stop_hook_blocks_and_uses_codex_toxi_home(tmp_path):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        '[mcp_servers.toxi]\ncommand = "toxi"\nenv = { TOXI_HOME = "/x/toxi-codex" }\n')
    r = _run(CODEX_HOOK, {"TOXI_BIN": _fake_toxi(tmp_path, "[toxi] hi"),
                          "CODEX_HOME": str(codex_home)})
    assert json.loads(r.stdout) == {"decision": "block", "reason": "[toxi] hi"}
    assert (tmp_path / "home_seen").read_text() == "/x/toxi-codex"


def test_codex_stop_hook_falls_back_to_statusline(tmp_path):
    r = _run(CODEX_HOOK, {"TOXI_BIN": _fake_toxi(tmp_path, ""),
                          "CODEX_HOME": str(tmp_path)})
    assert json.loads(r.stdout)["systemMessage"] == "toxi: 1/1 online"


def _dirs_config(tmp_path, monkeypatch, peer_list, dirs):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    lines = [f"[agents]\npeers = {json.dumps(peer_list)}\n", "[agents.dirs]\n"]
    lines += [f"{k} = {json.dumps(v)}\n" for k, v in dirs.items()]
    (tmp_path / "config.toml").write_text("".join(lines))


def test_dir_bound_peer_only_delivered_to_session_started_there(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    _dirs_config(tmp_path, monkeypatch, ["codex"], {"codex": str(proj)})
    _fake_daemon(monkeypatch, MSGS)
    assert peers.take(cwd=str(tmp_path)) == []  # another session's dir: left unread
    assert peers.take(cwd=str(proj / "sub")) == []  # a session started in a subdir
    got = peers.take(cwd=str(proj))
    assert [m["content"] for m in got] == ["first", "second"]


def test_dir_binding_ignored_without_cwd_and_for_unbound_peers(tmp_path, monkeypatch):
    _dirs_config(tmp_path, monkeypatch, ["codex", "bob"], {"codex": str(tmp_path / "x")})
    _fake_daemon(monkeypatch, MSGS)
    assert [m["alias"] for m in peers.take(cwd=str(tmp_path))] == ["bob"]  # unbound: anywhere
    assert [m["alias"] for m in peers.take()] == ["codex", "codex"]  # no cwd (MCP): no filter


def test_claude_stop_hook_passes_session_cwd(tmp_path):
    p = tmp_path / "faketoxi"
    p.write_text("#!/usr/bin/env python3\nimport sys\n"
                 f"open({str(tmp_path / 'args')!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n")
    p.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("TOXI_HOME", "CLAUDE_PROJECT_DIR")}
    env["TOXI_BIN"] = str(p)
    run = lambda e: subprocess.run([sys.executable, str(CC_HOOK)], capture_output=True,
                                   input=json.dumps({"cwd": "/w/proj/apple"}), text=True, env=e)
    run(env)  # no CLAUDE_PROJECT_DIR: fall back to the hook input's cwd
    run(dict(env, CLAUDE_PROJECT_DIR="/w/proj"))  # preferred: the launch dir
    calls = (tmp_path / "args").read_text().splitlines()
    assert "peer-inbox --cwd /w/proj/apple" in calls
    assert "peer-inbox --cwd /w/proj" in calls


def test_target_takes_the_full_session_label():
    assert peers.target({"content": "@those-moments@ada done"}) == "those-moments"
    assert peers.target({"content": "@OnesVibe hi"}) == "onesvibe"
