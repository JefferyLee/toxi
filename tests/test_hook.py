"""Tests for the Claude Code SessionStart hook (integrations/claude-code).

We point the hook at a fake `toxi` (via TOXI_BIN) so the tests are fast and need
no daemon or DHT.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "claude-code-plugin" / "hooks" / "unread_hook.py"


def _fake_toxi(tmp_path) -> str:
    p = tmp_path / "faketoxi"
    p.write_text("#!/usr/bin/env python3\nimport os, sys\n"
                 "if sys.argv[1:2] == ['peer-inbox']: print(os.environ.get('FAKE_PEER', ''))\n"
                 "else: print(os.environ.get('FAKE_OUT', ''))\n")
    p.chmod(0o755)
    return str(p)


def _run(env_extra) -> subprocess.CompletedProcess:
    env = dict(os.environ, **env_extra)
    return subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env)


def test_hook_emits_context_for_unread(tmp_path):
    r = _run({
        "TOXI_BIN": _fake_toxi(tmp_path),
        "FAKE_OUT": json.dumps([
            {"alias": "bob", "content": "hello"},
            {"alias": "carol", "content": "hola"},
        ]),
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "2 unread" in ctx
    assert "bob: hello" in ctx and "carol: hola" in ctx


def test_hook_describes_media_messages(tmp_path):
    r = _run({
        "TOXI_BIN": _fake_toxi(tmp_path),
        "FAKE_OUT": json.dumps([
            {"alias": "bob", "msg_type": "text", "content": "hi"},
            {"alias": "carol", "msg_type": "image", "content": "/m/pic.png"},
            {"alias": "dave", "msg_type": "voice", "content": "/m/clip.ogg"},
        ]),
    })
    assert r.returncode == 0
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "bob: hi" in ctx
    assert "carol sent an image (saved at: /m/pic.png)" in ctx
    assert "dave sent a voice message (saved at: /m/clip.ogg)" in ctx


def test_hook_silent_when_no_unread(tmp_path):
    r = _run({"TOXI_BIN": _fake_toxi(tmp_path), "FAKE_OUT": "[]"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_hook_silent_when_toxi_missing(tmp_path):
    r = _run({"TOXI_BIN": "/nonexistent/toxi-xyz"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_hook_delivers_routed_peer_messages_first(tmp_path):
    peer = "[toxi] 1 new message(s) from peer agents (trust: observer):\n- codex: hi"
    r = _run({"TOXI_BIN": _fake_toxi(tmp_path), "FAKE_OUT": "[]", "FAKE_PEER": peer,
              "CLAUDE_PROJECT_DIR": "/w/p"})
    assert json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] == peer

    r = _run({"TOXI_BIN": _fake_toxi(tmp_path), "FAKE_PEER": peer,
              "FAKE_OUT": json.dumps([{"alias": "bob", "content": "hello"}])})
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.index("[toxi] 1 new") < ctx.index("bob: hello")
