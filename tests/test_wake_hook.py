"""Claude Code idle wake-up (design §13.9): the asyncRewake waiter in
claude-code-plugin/hooks/wake_hook.py, against a fake `toxi`."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude-code-plugin" / "hooks" / "wake_hook.py"


def _setup(tmp_path, mark_fails=False):
    home, tmp = tmp_path / "home", tmp_path / "tmp"
    home.mkdir()
    tmp.mkdir()
    (home / "inbox.flag").touch()
    fake = tmp_path / "faketoxi"
    fake.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"mail = {str(tmp_path / 'mail.json')!r}\n"
        "if sys.argv[1:3] == ['peer-inbox', '--peek']:\n"
        "    try: print(open(mail).read())\n"
        "    except OSError: print(json.dumps({'text': '', 'msg_uuids': []}))\n"
        "elif sys.argv[1:2] == ['mark-read']:\n"
        f"    open({str(tmp_path / 'marked')!r}, 'a').write(' '.join(sys.argv[2:]) + chr(10))\n"
        f"    sys.exit({1 if mark_fails else 0})\n")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("TOXI_HOME", "CLAUDE_PROJECT_DIR")}
    env.update(TOXI_HOME=str(home), TMPDIR=str(tmp), TOXI_BIN=str(fake),
               TOXI_WAKE_POLL="0.05", TOXI_WAKE_SETTLE="0.2")
    return env


_procs = []


@pytest.fixture(autouse=True)
def _no_waiter_left_behind():
    yield
    while _procs:
        p = _procs.pop()
        if p.poll() is None:
            p.kill()
            p.wait()


def _start(env, session="s1", *args, **hook_input):
    p = subprocess.Popen([sys.executable, str(HOOK), *args], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    _procs.append(p)
    p.stdin.write(json.dumps({"session_id": session, "cwd": "/w/p", **hook_input}))
    p.stdin.close()
    return p


def _mail(tmp_path, text, uuids, wake=True):
    (tmp_path / "mail.json").write_text(json.dumps({"text": text, "msg_uuids": uuids, "wake": wake}))
    flag = tmp_path / "home" / "inbox.flag"
    t = time.time() + 1
    os.utime(flag, (t, t))


def _alive_after(p, seconds):
    try:
        p.wait(timeout=seconds)
        return False
    except subprocess.TimeoutExpired:
        return True


def test_it_waits_quietly_then_wakes_the_session_with_the_new_mail(tmp_path):
    env = _setup(tmp_path)
    p = _start(env)
    assert _alive_after(p, 0.6)  # nothing for this session: no wake
    _mail(tmp_path, "[toxi] 1 new message(s) from peer agents", ["u1"])
    assert p.wait(timeout=5) == 2
    assert p.stderr.read() == "[toxi] 1 new message(s) from peer agents"
    assert (tmp_path / "marked").read_text() == "-- u1\n"


def test_mail_already_there_is_picked_up_after_the_events_own_hooks(tmp_path):
    env = _setup(tmp_path)
    (tmp_path / "mail.json").write_text(json.dumps({"text": "waiting", "msg_uuids": ["u0"]}))
    assert _start(env).wait(timeout=5) == 2


def test_one_waiter_per_session_and_a_prompt_disarms_it(tmp_path):
    env = _setup(tmp_path)
    a = _start(env, "s1")
    assert _alive_after(a, 0.4)
    other = _start(env, "s2")  # another session keeps its own
    b = _start(env, "s1")
    assert a.wait(timeout=5) != 2  # replaced
    assert _alive_after(b, 0.4) and other.poll() is None
    assert _start(env, "s1", "--disarm").wait(timeout=5) == 0
    assert b.wait(timeout=5) != 2  # ended, and woke nothing


def test_a_subagent_never_waits(tmp_path):
    env = _setup(tmp_path)
    (tmp_path / "mail.json").write_text(json.dumps({"text": "x", "msg_uuids": ["u"]}))
    assert _start(env, "s1", agent_id="sub-1").wait(timeout=5) == 0
    assert not (tmp_path / "marked").exists()


def test_a_batch_that_will_not_mark_read_wakes_only_once(tmp_path):
    env = _setup(tmp_path, mark_fails=True)
    _mail(tmp_path, "stuck", ["u9"])
    assert _start(env).wait(timeout=5) == 2
    again = _start(env)  # Stop re-arms; the same batch is still unread
    assert _alive_after(again, 0.8)
    _start(env, "s1", "--disarm").wait(timeout=5)
    again.wait(timeout=5)


def test_wakes_stop_after_the_limit_until_the_user_types(tmp_path):
    env = dict(_setup(tmp_path), TOXI_WAKE_LIMIT="2")
    for i in range(2):
        _mail(tmp_path, f"ping {i}", [f"u{i}"])
        assert _start(env).wait(timeout=5) == 2
    _mail(tmp_path, "ping 2", ["u2"])
    assert _start(env).wait(timeout=5) == 0  # limit reached: not armed
    assert _start(env, "s1", "--disarm").wait(timeout=5) == 0  # the user typed
    assert _start(env).wait(timeout=5) == 2


def test_mail_that_does_not_wake_waits_unread_until_mail_that_does(tmp_path):
    env = _setup(tmp_path)
    p = _start(env)
    _mail(tmp_path, "FYI: nightly build green", ["r1"], wake=False)
    assert _alive_after(p, 0.6)  # an FYI report does not wake the session
    assert not (tmp_path / "marked").exists()  # and stays unread for the next turn
    _mail(tmp_path, "FYI: nightly build green\n- please rerun", ["r1", "q1"], wake=True)
    assert p.wait(timeout=5) == 2
    assert (tmp_path / "marked").read_text() == "-- r1 q1\n"
