"""Tests for the Codex plugin package.

These stay local and fast: hook tests point at a fake `toxi` binary through
TOXI_BIN, so no daemon, DHT, or installed Codex client is required.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "toxi"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _fake_toxi(tmp_path, payload: str, statusline: str = "toxi: 0/0 online") -> str:
    p = tmp_path / "faketoxi"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"payload = {payload!r}\n"
        f"statusline = {statusline!r}\n"
        "if args == ['statusline']:\n"
        "    print(statusline)\n"
        "elif args == ['--json', 'unread']:\n"
        "    print(payload)\n"
        "else:\n"
        "    raise SystemExit(2)\n"
    )
    p.chmod(0o755)
    return str(p)


def _run_hook(name: str, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ, **env_extra)
    return subprocess.run(
        [sys.executable, str(PLUGIN / "hooks" / name)],
        capture_output=True,
        text=True,
        env=env,
    )


def _hook_output(res: subprocess.CompletedProcess) -> dict:
    return json.loads(res.stdout)["hookSpecificOutput"]


def test_codex_plugin_manifest_points_to_components():
    manifest = _load(PLUGIN / ".codex-plugin" / "plugin.json")

    assert manifest["name"] == "toxi"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["displayName"] == "Toxi"


def test_codex_plugin_mcp_runs_toxi_server():
    mcp = _load(PLUGIN / ".mcp.json")
    server = mcp["mcpServers"]["toxi"]

    assert server["command"] == "toxi"
    assert server["args"] == ["mcp", "serve"]


def test_codex_marketplace_exposes_toxi_plugin():
    marketplace = _load(ROOT / ".agents" / "plugins" / "marketplace.json")
    entry = marketplace["plugins"][0]

    assert entry["name"] == "toxi"
    assert entry["source"] == {"source": "local", "path": "./plugins/toxi"}
    assert entry["policy"]["installation"] == "AVAILABLE"


def test_codex_hooks_cover_start_tool_and_stop():
    hooks = _load(PLUGIN / "hooks" / "hooks.json")["hooks"]

    assert set(hooks) == {"SessionStart", "PostToolUse", "Stop"}
    assert "session_start_hook.py" in hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "peer_check.sh" in hooks["PostToolUse"][0]["hooks"][0]["command"]
    for name in ("session_start_hook.py", "peer_check.sh", "post_tool_hook.py", "stop_hook.py"):
        assert (PLUGIN / "hooks" / name).exists()


def test_codex_hook_commands_resolve_outside_the_repo(tmp_path):
    # Codex sets PLUGIN_ROOT (not CODEX_PLUGIN_ROOT) and runs hooks in the
    # session's cwd, so a relative fallback alone breaks in every other project.
    # A script that cannot be found must exit 0: Codex reads exit 2 from a Stop
    # hook as "do not stop" and would loop on the error.
    import subprocess
    hooks = _load(PLUGIN / "hooks" / "hooks.json")["hooks"]
    for event in ("SessionStart", "PostToolUse", "Stop"):
        cmd = hooks[event][0]["hooks"][0]["command"]
        assert "${PLUGIN_ROOT:-" in cmd
        probe = cmd.split(";")[0] + '; printf %s "$f"'
        env = {"PATH": "/usr/bin:/bin", "PLUGIN_ROOT": str(PLUGIN)}
        out = subprocess.run(["sh", "-c", probe], cwd=tmp_path, capture_output=True, text=True, env=env).stdout
        assert Path(out).exists(), out
        lost = subprocess.run(["sh", "-c", cmd], cwd=tmp_path, capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin"}, input="{}")
        assert lost.returncode == 0 and not lost.stderr, (event, lost.returncode, lost.stderr)

def test_codex_stop_hook_emits_statusline_context(tmp_path):
    res = _run_hook(
        "stop_hook.py",
        {"TOXI_BIN": _fake_toxi(tmp_path, "[]", "toxi: 1/1 online")},
    )

    assert res.returncode == 0
    assert json.loads(res.stdout) == {
        "continue": True,
        "suppressOutput": True,
        "systemMessage": "toxi: 1/1 online",
    }
    assert res.stderr == ""


def test_codex_unread_hook_emits_untrusted_context(tmp_path):
    msgs = json.dumps([{"alias": "bob", "content": "hello"}])
    res = _run_hook(
        "unread_hook.py",
        {"TOXI_BIN": _fake_toxi(tmp_path, msgs, "toxi: 📬 1 from bob · 1/1 online")},
    )

    assert res.returncode == 0
    out = _hook_output(res)
    ctx = out["additionalContext"]
    assert out["hookEventName"] == "SessionStart"
    assert "toxi: 📬 1 from bob · 1/1 online" in ctx
    assert "1 unread" in ctx
    assert "bob: hello" in ctx
    assert "untrusted personal content" in ctx
