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
    p.write_text("#!/usr/bin/env python3\nimport sys\n"
                 f"open({str(tmp_path / 'args')!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
                 f"print({text!r})\n")
    p.chmod(0o755)
    return str(p)


def _run(tmp_path, home, project):
    env = {k: v for k, v in os.environ.items() if k not in ("TOXI_HOME", "CLAUDE_PROJECT_DIR")}
    env.update(TOXI_HOME=str(home), TMPDIR=str(tmp_path / "tmp"), CLAUDE_PROJECT_DIR=str(project),
               TOXI_BIN=_fake_toxi(tmp_path, "[toxi] 1 new message(s) from peer agents (trust: observer):\n- codex: hi"))
    return subprocess.run(["sh", str(CHECK)], capture_output=True, text=True, env=env,
                          input=json.dumps({"cwd": str(project)}))


def test_delivers_only_when_the_flag_moved(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); project.mkdir(); (tmp_path / "tmp").mkdir()
    assert _run(tmp_path, home, project).stdout == ""  # no flag yet: not even a python start
    assert not (tmp_path / "args").exists()

    (home / "inbox.flag").touch()
    out = json.loads(_run(tmp_path, home, project).stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "- codex: hi" in out["hookSpecificOutput"]["additionalContext"]
    assert (tmp_path / "args").read_text().strip() == f"peer-inbox --cwd {project}"

    assert _run(tmp_path, home, project).stdout == ""  # unchanged flag: silent
    time.sleep(1.1)  # mtime resolution
    (home / "inbox.flag").touch()
    assert "additionalContext" in _run(tmp_path, home, project).stdout
    # Another project has its own marker and gets its own delivery attempt.
    other = tmp_path / "other"; other.mkdir()
    assert "additionalContext" in _run(tmp_path, home, other).stdout
