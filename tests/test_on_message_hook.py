"""[hooks] on_message: the daemon runs a command when new messages arrive.

Exercises the hook machinery directly (no toxcore): a Daemon built without
__init__, with only the hook fields set.
"""
import json
import time

from toxi import daemon as daemon_mod
from toxi.daemon import Daemon


def _daemon():
    d = Daemon.__new__(Daemon)
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    return d


def _config(tmp_path, monkeypatch, cmd):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    if cmd is not None:
        (tmp_path / "config.toml").write_text(f"[hooks]\non_message = {json.dumps(cmd)}\n")


def _drain(d, timeout=5.0):
    """Tick until the hook has fired and its process has been reaped."""
    d._hook_due = 0.0
    deadline = time.time() + timeout
    d._tick_hook()
    while d._hook_proc is not None and time.time() < deadline:
        time.sleep(0.05)
        d._tick_hook()
    assert d._hook_proc is None


def test_hook_coalesces_burst_and_passes_metadata_only(tmp_path, monkeypatch):
    out = tmp_path / "out.txt"
    _config(tmp_path, monkeypatch, f'(echo "$TOXI_FROM|$TOXI_COUNT"; cat) > {out}')
    d = _daemon()
    d._queue_hook("codex", "u1", "text")
    d._queue_hook("codex", "u2", "text")
    d._queue_hook("bob", "u3", "image")
    _drain(d)
    head, stdin = out.read_text().split("\n", 1)
    assert head == "codex,bob|3"
    assert json.loads(stdin) == [
        {"alias": "codex", "msg_uuid": "u1", "msg_type": "text"},
        {"alias": "codex", "msg_uuid": "u2", "msg_type": "text"},
        {"alias": "bob", "msg_uuid": "u3", "msg_type": "image"},
    ]
    assert d._hook_msgs == []


def test_hook_waits_for_quiet_period(tmp_path, monkeypatch):
    out = tmp_path / "out.txt"
    _config(tmp_path, monkeypatch, f"touch {out}")
    d = _daemon()
    d._queue_hook("codex", "u1", "text")
    d._tick_hook()  # still inside HOOK_DEBOUNCE
    assert d._hook_proc is None and not out.exists()
    assert d._hook_due > time.time()


def test_no_hook_configured_drops_queue(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, None)
    d = _daemon()
    d._queue_hook("codex", "u1", "text")
    _drain(d)
    assert d._hook_msgs == []


def test_one_hook_at_a_time_and_timeout_kills(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, "sleep 30")
    monkeypatch.setattr(daemon_mod, "HOOK_TIMEOUT", 0.2)
    d = _daemon()
    d._queue_hook("codex", "u1", "text")
    d._hook_due = 0.0
    d._tick_hook()
    first = d._hook_proc
    assert first is not None
    d._queue_hook("codex", "u2", "text")  # arrives while the hook still runs
    d._hook_due = 0.0
    d._tick_hook()
    assert d._hook_proc is first and len(d._hook_msgs) == 1  # held, not started
    time.sleep(0.3)
    d._tick_hook()  # past HOOK_TIMEOUT: killed
    first.wait(timeout=5)
    d._tick_hook()  # reaped, then the held message fires a fresh hook
    assert d._hook_proc is not first and d._hook_msgs == []
    d._hook_proc.kill()
