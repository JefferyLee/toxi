"""One daemon per identity (2026-10-22 incident: a second daemon started while
the first was too busy to answer, and both ran the same Tox key for 5 days)."""
import pytest
from click.testing import CliRunner

from toxi import bootstrap, daemon
from toxi.cli import cli


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    return tmp_path


def test_second_daemon_cannot_take_the_lock(home):
    assert not bootstrap.daemon_locked()
    held = daemon.hold_instance_lock()
    try:
        assert bootstrap.daemon_locked()
        with pytest.raises(SystemExit, match="already running"):
            daemon.hold_instance_lock(attempts=2)
    finally:
        held.close()
    assert not bootstrap.daemon_locked()
    daemon.hold_instance_lock().close()  # free again once the holder is gone


def test_start_refuses_when_a_silent_daemon_holds_the_lock(home, monkeypatch):
    held = daemon.hold_instance_lock()
    spawned = []
    monkeypatch.setattr("toxi.cli.bootstrap.daemon_running", lambda: False)  # busy: no answer
    monkeypatch.setattr("toxi.cli.subprocess.Popen", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr("toxi.cli.time.time", iter(range(0, 1000, 10)).__next__)  # skip the wait
    try:
        res = CliRunner().invoke(cli, ["daemon", "start"])
        assert res.exit_code != 0 and "Not starting a second one" in res.output
        assert spawned == []
    finally:
        held.close()


def test_doctor_reports_a_silent_lock_holder(home, monkeypatch):
    held = daemon.hold_instance_lock()
    monkeypatch.setattr("toxi.cli.bootstrap.daemon_running", lambda: False)
    try:
        doc = CliRunner().invoke(cli, ["doctor"])
        assert "holds the lock but does not answer" in doc.output
    finally:
        held.close()
