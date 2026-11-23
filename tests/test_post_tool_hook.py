"""Claude Code PostToolUse delivery: peer_check.sh gates on inbox.flag, then the
python hook delivers `toxi peer-inbox` output as additionalContext."""
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "claude-code-plugin" / "hooks" / "peer_check.sh"


def _fake_toxi(tmp_path, text):
    p = tmp_path / "faketoxi"
    p.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                 f"open({str(tmp_path / 'args')!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
                 "if sys.argv[1:3] == ['peer-inbox', '--peek']:\n"
                 f"    print(json.dumps({{'text': {text!r}, 'msg_uuids': ['u1']}}))\n")
    p.chmod(0o755)
    return str(p)


def _run(tmp_path, home, project, **hook_input):
    env = {k: v for k, v in os.environ.items() if k not in ("TOXI_HOME", "CLAUDE_PROJECT_DIR")}
    env.update(TOXI_HOME=str(home), TMPDIR=str(tmp_path / "tmp"), CLAUDE_PROJECT_DIR=str(project),
               TOXI_BIN=_fake_toxi(tmp_path, "[toxi] 1 new message(s) from peer agents (trust: observer):\n- codex: hi"))
    return subprocess.run(["sh", str(CHECK)], capture_output=True, text=True, env=env,
                          input=json.dumps({**hook_input, "cwd": str(project), "tool_input": {}}))


def test_delivers_only_when_the_flag_moved(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); project.mkdir(); (tmp_path / "tmp").mkdir()
    assert _run(tmp_path, home, project).stdout == ""  # no flag yet: not even a python start
    assert not (tmp_path / "args").exists()

    (home / "inbox.flag").touch()
    out = json.loads(_run(tmp_path, home, project).stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "- codex: hi" in out["hookSpecificOutput"]["additionalContext"]
    assert (tmp_path / "args").read_text().splitlines() == [f"peer-inbox --peek --cwd {project}",
                                                            "mark-read -- u1"]

    assert _run(tmp_path, home, project).stdout == ""  # unchanged flag: silent
    time.sleep(1.1)  # mtime resolution
    (home / "inbox.flag").touch()
    assert "additionalContext" in _run(tmp_path, home, project).stdout
    # Another project has its own marker and gets its own delivery attempt.
    other = tmp_path / "other"; other.mkdir()
    assert "additionalContext" in _run(tmp_path, home, other).stdout


def test_subagents_leave_the_mail_and_sessions_in_one_dir_each_look(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); project.mkdir(); (tmp_path / "tmp").mkdir()
    (home / "inbox.flag").touch()
    # A subagent's tool call takes nothing, and does not hide the mail from the main session.
    assert _run(tmp_path, home, project, session_id="A", agent_id="sub-1").stdout == ""
    assert not (tmp_path / "args").exists()
    assert "additionalContext" in _run(tmp_path, home, project, session_id="A").stdout
    # A second session started in the same dir still gets its own look.
    assert "additionalContext" in _run(tmp_path, home, project, session_id="B").stdout
    assert _run(tmp_path, home, project, session_id="A").stdout == ""
