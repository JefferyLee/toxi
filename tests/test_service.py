"""`toxi service`: launchd / systemd user-service definitions (design §4.7)."""
import plistlib
import subprocess

from toxi import service


def test_label_and_paths_follow_toxi_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOXI_HOME", raising=False)
    assert service.tag() is None and service.label() == "dev.toxi.daemon"
    assert service.unit_name() == "toxi-daemon"
    monkeypatch.setenv("TOXI_HOME", str(tmp_path / ".config" / "toxi-codex"))
    assert service.tag() == "toxi-codex" and service.label() == "dev.toxi.daemon.toxi-codex"
    assert service.unit_path().name == "toxi-daemon-toxi-codex.service"


def test_rendered_definitions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOXI_HOME", str(tmp_path / "home"))
    pl = plistlib.loads(service.render_plist())
    assert pl["Label"] == "dev.toxi.daemon.home" and pl["KeepAlive"] is True and pl["RunAtLoad"] is True
    prog = pl["ProgramArguments"]
    assert prog[0].endswith("toxi-daemon") or prog[1:] == ["-m", "toxi.daemon"]
    assert pl["EnvironmentVariables"]["TOXI_HOME"] == str(tmp_path / "home")
    unit = service.render_unit()
    assert ("toxi-daemon" in unit or "-m toxi.daemon" in unit) and "Restart=always" in unit
    assert "TOXI_HOME=" in unit


def test_install_status_uninstall_macos(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOXI_HOME", raising=False)
    monkeypatch.setattr(service, "is_macos", lambda: True)
    monkeypatch.setattr(service, "manager", lambda: "/bin/launchctl")
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        out = "state = running\n\tpid = 4242\n" if cmd[:2] == ["launchctl", "print"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(service, "_run", fake_run)
    p = service.install()
    assert p.exists() and p.name == "dev.toxi.daemon.plist"
    assert calls[-1][:2] == ["launchctl", "bootstrap"]
    st = service.status()
    assert st == {"installed": True, "running": True, "pid": 4242, "path": str(p)}
    service.restart()
    assert calls[-1][:3] == ["launchctl", "kickstart", "-k"]
    assert service.uninstall() is True and not p.exists()
    assert service.uninstall() is False
    assert service.status()["installed"] is False


def test_install_failure_is_reported_and_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service, "is_macos", lambda: False)
    monkeypatch.setattr(service, "manager", lambda: "/bin/systemctl")
    monkeypatch.setattr(service, "_run", lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "no systemd"))
    try:
        service.install()
    except service.ServiceError as e:
        assert "no systemd" in str(e)
    else:
        raise AssertionError("expected ServiceError")
    assert not service.service_path().exists() and service.installed() is False


def test_no_service_manager_means_not_installable_and_not_installed(tmp_path, monkeypatch):
    """A Linux VM without systemd (the Grok bots' case): install refuses up front,
    a stale unit file from an older build does not count as installed, and
    uninstall still removes it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service, "is_macos", lambda: False)
    monkeypatch.setattr(service, "manager", lambda: None)
    try:
        service.install()
    except service.ServiceError as e:
        assert "systemctl not found" in str(e)
    else:
        raise AssertionError("expected ServiceError")
    assert not service.service_path().exists()
    service.service_path().parent.mkdir(parents=True)
    service.service_path().write_text("[Unit]\n")  # left behind by the buggy build
    assert service.installed() is False
    assert service.uninstall() is True and not service.service_path().exists()


def test_run_reports_a_missing_manager_binary():
    r = service._run(["definitely-not-a-command-xyz", "--user"])
    assert r.returncode == 127 and "command not found" in r.stderr
