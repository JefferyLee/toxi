"""0.7 operations (design §13.6, batch 2): what the tooling does when the
machine is not as expected."""
import subprocess
import sys
from pathlib import Path


def test_without_libtoxcore_the_cli_still_imports_and_doctor_says_so(tmp_path):
    code = (
        "import toxi.tox as t\n"
        "def nope(): raise t.ToxError('could not load libtoxcore')\n"
        "t._load_libtoxcore = nope\n"
        "import toxi.cli, toxi.daemon, toxi.mcp_server\n"
        "checks = {c['check']: c for c in toxi.cli._doctor_checks()}\n"
        "print(checks['libtoxcore']['ok'], checks['libtoxcore']['detail'])\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={"TOXI_HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.startswith("False could not load libtoxcore")


def _install_sh_python():
    """The Python that install.sh runs to register the Claude Code plugin."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
    return text.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def test_install_sh_never_overwrites_a_settings_file_it_cannot_read(tmp_path):
    import json
    claude = tmp_path / ".claude"
    claude.mkdir()
    settings = claude / "settings.json"
    run = lambda: subprocess.run(  # noqa: E731
        [sys.executable, "-c", _install_sh_python(), "0.7.0", "abc", "/x", "/m", "o/r"],
        capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    settings.write_text('{"model": "opus", ')  # broken
    r = run()
    assert r.returncode != 0 and "not valid JSON" in r.stderr
    assert settings.read_text() == '{"model": "opus", '
    settings.write_text('{"model": "opus"}')
    assert run().returncode == 0
    assert json.loads(settings.read_text()) == {"model": "opus", "enabledPlugins": {"toxi@toxi": True}}
    assert json.loads((claude / "settings.json.bak-toxi").read_text()) == {"model": "opus"}


def test_trace_shows_each_step_and_the_log_lines(tmp_path, monkeypatch):
    import json
    from click.testing import CliRunner
    from toxi.cli import cli
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "daemon.log").write_text(
        "2026-11-22 10:00:00,001 INFO sent abcd1234 to codex\n"
        "2026-11-22 10:00:01,001 INFO received ffff0000 from bob\n"
        "2026-11-22 10:05:00,001 INFO ACK for abcd1234 from codex\n")
    msg = {"msg_uuid": "abcd1234-0000", "alias": "codex", "direction": "out", "status": "delivered",
           "created_at": 1000, "sent_at": 1000, "last_attempt_at": 1300, "delivered_at": 1301}
    monkeypatch.setattr("toxi.client.request", lambda m, p=None, **k: {"message": msg})
    out = CliRunner().invoke(cli, ["trace", "abcd1234-0000"]).output
    assert "to codex · delivered" in out and "+   301s  delivered (ACK)" in out
    assert "ACK for abcd1234 from codex" in out and "ffff0000" not in out
    data = json.loads(CliRunner().invoke(cli, ["--json", "trace", "abcd1234-0000"]).output)
    assert data["steps"]["delivered_at"] == 1301 and len(data["log"]) == 2


def test_a_daemon_that_cannot_start_says_why(tmp_path, monkeypatch):
    from toxi import cli, client, service
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "service.log").write_text(
        "Traceback (most recent call last):\n  File \"daemon.py\", line 1, in <module>\n"
        "ImportError: dlopen(_sqlite3.so): Library not loaded: libsqlite3.dylib\n")
    assert service.last_error().startswith("ImportError: dlopen(_sqlite3.so)")
    monkeypatch.setattr(service, "installed", lambda: True)

    def down(*a, **k):
        raise client.DaemonNotRunning("no daemon")
    monkeypatch.setattr("toxi.client.request", down)
    assert "toxi stopped: ImportError: dlopen" in cli._toxi_segment()
    (tmp_path / "service.log").write_text("all quiet\n")
    assert "toxi: offline" in cli._toxi_segment()


# --- toxi upgrade / doctor --all ------------------------------------------------

def _fake_bin(dir_, name, script):
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(0o755)
    return p


def test_upgrade_installs_again_when_the_index_still_serves_the_old_version(tmp_path, monkeypatch):
    from toxi import cli
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    count = tmp_path / "count"
    # pipx "installs"; toxi reports the old version the first time, the new one after.
    _fake_bin(bin_, "pipx", f'case "$1" in environment) echo "{bin_}";; list) echo "toxi 0.8.0";; '
                            f'*) echo x >> "{count}";; esac\n')
    _fake_bin(bin_, "toxi", f'n=$(wc -l < "{count}"); [ "$n" -ge 2 ] && echo "toxi, version 0.9.0" '
                            '|| echo "toxi, version 0.8.0"\n')
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli._upgrade_install("0.9.0")
    assert count.read_text().count("x") == 2


def test_upgrade_moves_a_pinned_codex_plugin_to_the_release(tmp_path, monkeypatch):
    from toxi import cli
    (tmp_path / "config.toml").write_text(
        '[marketplaces.toxi]\nsource_type = "git"\nsource = "https://github.com/JefferyLee/toxi.git"\nref = "v0.6.14"\n')
    log = tmp_path / "codex.log"
    codex = _fake_bin(tmp_path, "codex", f'echo "$@" >> "{log}"\n')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_codex_bin", lambda: str(codex))
    cli._upgrade_codex_plugin("0.7.0")
    assert log.read_text().splitlines() == [
        "plugin marketplace remove toxi",
        "plugin marketplace add https://github.com/JefferyLee/toxi.git --ref v0.7.0",
        "plugin add toxi@toxi"]
    (tmp_path / "config.toml").write_text('[marketplaces.toxi]\nsource_type = "git"\nref = "v0.7.0"\n')
    log.unlink()
    cli._upgrade_codex_plugin("0.7.0")  # already there: nothing to do
    assert not log.exists()


def test_upgrade_restarts_each_identity_under_its_service(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from toxi import cli, service
    homes = [tmp_path / "a", tmp_path / "b"]
    monkeypatch.setattr(service, "homes", lambda: homes)
    monkeypatch.setattr(service, "installed", lambda: cli.os.environ["TOXI_HOME"].endswith("a"))
    monkeypatch.setattr(cli, "_stale_mcp", lambda: [{"pid": 9, "agent": {"pid": 7, "command": "claude"}}])
    monkeypatch.setattr(cli, "_upgrade_codex_plugin", lambda v: None)
    monkeypatch.setattr(cli.bootstrap, "toxi_plugin_cache_root", lambda: tmp_path / "none")
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "✓ daemon is up under the service manager\n", ""

    monkeypatch.setattr(cli.subprocess, "run", lambda args, env=None, **k: calls.append((args[1:], env["TOXI_HOME"])) or Done())
    out = CliRunner().invoke(cli.cli, ["upgrade", "--installed", "--all"]).output
    assert calls == [(["service", "install"], str(homes[0]))]
    assert f"✓ {homes[0]}: ✓ daemon is up" in out and f"ℹ {homes[1]}: no service" in out
    assert "claude pid 7" in out


def test_doctor_sorts_plugin_versions_as_versions(tmp_path, monkeypatch):
    from toxi import cli
    cache = tmp_path / "cache"
    for v in ("0.6.9", "0.6.14", "0.6.10"):
        (cache / v / "hooks").mkdir(parents=True)
        (cache / v / "hooks" / "hooks.json").write_text("wake_hook")
    monkeypatch.setattr(cli.bootstrap, "toxi_plugin_cache_root", lambda: cache)
    monkeypatch.setattr(cli, "__version__", "0.6.14")
    monkeypatch.setattr(cli, "_stale_mcp", lambda: [])
    monkeypatch.setattr(cli, "_codex_marketplace", lambda: None)
    plugin = next(c for c in cli._machine_checks() if c["check"] == "claude plugin")
    assert plugin["ok"] and plugin["detail"].startswith("0.6.14")


# --- toxi export / import --------------------------------------------------------

def test_export_and_import_move_an_identity(tmp_path, monkeypatch):
    import sqlite3
    import stat
    import tarfile
    from click.testing import CliRunner
    from toxi.cli import cli
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "media").mkdir(parents=True)
    (old / "tox_state.bin").write_bytes(b"KEY")
    (old / "config.toml").write_text('[peers.codex]\ntrust = "observer"\n')
    (old / "media" / "u1__pic.png").write_bytes(b"png")
    (old / "media" / "u2__half.png.part").write_bytes(b"x")
    (old / "daemon.log").write_text("not exported")
    db = sqlite3.connect(old / "chat.db")
    db.execute("CREATE TABLE t (x)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()
    db.close()
    backup = tmp_path / "ada.tar.gz"
    monkeypatch.setenv("TOXI_HOME", str(old))
    r = CliRunner().invoke(cli, ["export", str(backup)])
    assert r.exit_code == 0, r.output
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with tarfile.open(backup) as tar:
        names = set(tar.getnames())
    assert {"tox_state.bin", "config.toml", "chat.db", "media/u1__pic.png"} <= names
    assert "daemon.log" not in names and "media/u2__half.png.part" not in names

    monkeypatch.setenv("TOXI_HOME", str(new))
    r = CliRunner().invoke(cli, ["import", str(backup)])
    assert r.exit_code == 0, r.output
    assert (new / "tox_state.bin").read_bytes() == b"KEY"
    assert sqlite3.connect(new / "chat.db").execute("SELECT x FROM t").fetchone() == (42,)
    assert stat.S_IMODE((new / "tox_state.bin").stat().st_mode) == 0o600
    assert stat.S_IMODE(new.stat().st_mode) == 0o700
    r = CliRunner().invoke(cli, ["import", str(backup)])  # it has an identity now
    assert r.exit_code != 0 and "already has an identity" in r.output


def test_import_writes_nothing_outside_the_home(tmp_path, monkeypatch):
    import io
    import json
    import tarfile
    from click.testing import CliRunner
    from toxi.cli import cli
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        for name, data in (("toxi-export.json", json.dumps({"toxi_export": 1}).encode()),
                           ("../escaped.txt", b"x"), ("/tmp/abs-toxi-test.txt", b"x"),
                           ("tox_state.bin", b"KEY")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("media/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    home = tmp_path / "home"
    monkeypatch.setenv("TOXI_HOME", str(home))
    assert CliRunner().invoke(cli, ["import", str(evil)]).exit_code == 0
    assert (home / "tox_state.bin").exists()
    assert not (tmp_path / "escaped.txt").exists() and not Path("/tmp/abs-toxi-test.txt").exists()
    assert not (home / "media" / "link").exists()


def test_a_daemon_never_starts_where_there_is_no_identity(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from toxi.cli import cli
    monkeypatch.setenv("TOXI_HOME", str(tmp_path / "typo"))
    r = CliRunner().invoke(cli, ["daemon", "start"])
    assert r.exit_code != 0 and "no identity in" in r.output and "toxi init" in r.output


def test_upgrade_never_goes_back_and_stops_on_a_failing_install(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from toxi import cli
    monkeypatch.setattr(cli, "_editable", lambda: False)
    r = CliRunner().invoke(cli.cli, ["upgrade", "--version", "0.0.1"])
    assert r.exit_code != 0 and "no `upgrade --installed`" in r.output
    monkeypatch.setattr(cli, "__version__", "0.9.0")
    r = CliRunner().invoke(cli.cli, ["upgrade", "--version", "0.8.0"])
    assert r.exit_code != 0 and "not going back" in r.output
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _fake_bin(bin_, "pipx", f'case "$1" in environment) echo "{bin_}";; list) echo "toxi 0.8.0";; '
                            'install) echo "ERROR: Package requires a different Python" >&2; exit 1;; esac\n')
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    import pytest
    with pytest.raises(cli.click.ClickException) as e:
        cli._upgrade_install("0.9.1")
    assert "--python python3.12" in e.value.message


def test_import_takes_only_what_export_writes(tmp_path, monkeypatch):
    import io
    import json
    import tarfile
    from click.testing import CliRunner
    from toxi.cli import cli
    f = tmp_path / "x.tar.gz"
    with tarfile.open(f, "w:gz") as tar:
        for name, data in (("toxi-export.json", json.dumps({"toxi_export": 1}).encode()),
                           ("tox_state.bin", b"KEY"), ("daemon.lock", b"x"), ("statusline_wrapped", b"rm -rf ~")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    home = tmp_path / "home"
    monkeypatch.setenv("TOXI_HOME", str(home))
    assert CliRunner().invoke(cli, ["import", str(f)]).exit_code == 0
    assert sorted(p.name for p in home.iterdir()) == ["tox_state.bin"]
    (tmp_path / "plain.txt").write_text("not a tar")
    monkeypatch.setenv("TOXI_HOME", str(tmp_path / "empty"))
    r = CliRunner().invoke(cli, ["import", str(tmp_path / "plain.txt")])
    assert r.exit_code != 0 and "not a `toxi export` file" in r.output and "Traceback" not in r.output


def test_a_daemon_under_a_service_never_makes_a_new_identity(tmp_path):
    import os
    r = subprocess.run([sys.executable, "-m", "toxi.daemon"], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, TOXI_HOME=str(tmp_path / "moved"), TOXI_SERVICE="1"))
    assert r.returncode != 0 and "not making a new one" in r.stderr
    assert not (tmp_path / "moved" / "tox_state.bin").exists()


def test_last_error_ignores_an_error_older_than_the_daemons_log(tmp_path, monkeypatch):
    import os
    import time
    from toxi import service
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "service.log").write_text("ImportError: long ago\n")
    (tmp_path / "daemon.log").write_text("ran fine since\n")
    old = time.time() - 3600
    os.utime(tmp_path / "service.log", (old, old))
    assert service.last_error() is None
    os.utime(tmp_path / "service.log", None)
    os.utime(tmp_path / "daemon.log", (old, old))
    assert service.last_error() == "ImportError: long ago"
