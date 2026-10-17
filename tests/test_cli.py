"""CLI tests.

Most setup/Codex paths are pure unit tests. Tests that create a real identity or
start the daemon are marked `toxcore` and skipped unless pytest is run with
--run-toxcore.
"""
import shutil
import subprocess
import tempfile

import pytest
from click.testing import CliRunner

from toxi import bootstrap, paths
from toxi.cli import cli


@pytest.fixture
def home(monkeypatch):
    d = tempfile.mkdtemp(prefix="toxi-", dir="/tmp")  # short path for AF_UNIX
    monkeypatch.setenv("TOXI_HOME", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.toxcore
def test_init_creates_identity(home):
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert "Your Tox ID:" in result.output
    assert paths.tox_state_path().exists()

    again = runner.invoke(cli, ["init"])
    assert "Already initialized" in again.output


def test_me_requires_daemon(home):
    result = CliRunner().invoke(cli, ["me"])
    assert result.exit_code != 0
    assert "daemon is not running" in result.output


@pytest.mark.toxcore
def test_full_cli_lifecycle(home):
    runner = CliRunner()
    try:
        assert "daemon started" in runner.invoke(cli, ["daemon", "start"]).output

        me = runner.invoke(cli, ["me"])
        assert me.exit_code == 0
        # 38-byte Tox address printed as 76 hex chars
        line = next(l for l in me.output.splitlines() if l.startswith("Your Tox ID:"))
        assert len(line.split(": ", 1)[1]) == 76

        assert "set to: alice" in runner.invoke(cli, ["set-name", "alice"]).output
        assert "Display name: alice" in runner.invoke(cli, ["me"]).output

        st = runner.invoke(cli, ["status"])
        assert "DHT:" in st.output and "Queue: 0 messages pending" in st.output
    finally:
        runner.invoke(cli, ["daemon", "stop"])


def test_daemon_stop_when_not_running(home):
    assert "not running" in CliRunner().invoke(cli, ["daemon", "stop"]).output


@pytest.mark.toxcore
def test_error_message_is_clean(home):
    """CLI errors show the human message, not the internal error code."""
    runner = CliRunner()
    try:
        runner.invoke(cli, ["daemon", "start"])
        res = runner.invoke(cli, ["send", "ghost", "hi"])
        assert res.exit_code != 0
        assert "no contact named" in res.output
        assert "CONTACT_NOT_FOUND" not in res.output
    finally:
        runner.invoke(cli, ["daemon", "stop"])


def test_setup_engine_initializes_identity_and_daemon(home, monkeypatch):
    class FakeTox:
        def self_get_address_hex(self):
            return "A" * 76

        def get_savedata(self):
            return b"tox-state"

        def kill(self):
            pass

    monkeypatch.setattr("toxi.cli.Tox", FakeTox)
    monkeypatch.setattr("toxi.cli.bootstrap.identity_initialized", lambda: False)
    monkeypatch.setattr("toxi.cli.bootstrap.daemon_running", lambda: False)
    monkeypatch.setattr("toxi.cli._spawn_daemon", lambda: 1234)

    res = CliRunner().invoke(cli, ["setup-engine"])

    assert res.exit_code == 0
    assert "Generated identity" in res.output
    assert "Daemon started (PID 1234)" in res.output
    assert paths.tox_state_path().read_bytes() == b"tox-state"


def test_setup_claude_wires_statusline_without_engine(monkeypatch):
    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "present")
    monkeypatch.setattr("toxi.cli.bootstrap.ensure_statusline", lambda: "added")
    monkeypatch.setattr("toxi.cli.bootstrap.claude_settings_path", lambda: "/tmp/settings.json")
    monkeypatch.setattr("toxi.cli.Tox", lambda: (_ for _ in ()).throw(AssertionError("no Tox")))
    monkeypatch.setattr("toxi.cli._spawn_daemon", lambda: (_ for _ in ()).throw(AssertionError("no daemon")))

    res = CliRunner().invoke(cli, ["setup-claude"])

    assert res.exit_code == 0
    assert "MCP extra already installed" in res.output
    assert "Wired statusLine" in res.output
    assert "install the Claude Code plugin" in res.output


def test_setup_keeps_legacy_combined_flow(monkeypatch):
    calls = []
    monkeypatch.setattr("toxi.cli._setup_engine", lambda: calls.append("engine"))
    monkeypatch.setattr("toxi.cli._setup_claude", lambda: calls.append("claude"))

    res = CliRunner().invoke(cli, ["setup"])

    assert res.exit_code == 0
    assert calls == ["engine", "claude"]


def test_codex_repo_root_prefers_current_checkout(monkeypatch, tmp_path):
    installed = tmp_path / "venv" / "lib" / "python3.14"
    checkout = tmp_path / "checkout"
    nested = checkout / "docs"
    (checkout / ".agents" / "plugins").mkdir(parents=True)
    (checkout / ".agents" / "plugins" / "marketplace.json").write_text("{}")
    (checkout / "plugins" / "toxi" / ".codex-plugin").mkdir(parents=True)
    (checkout / "plugins" / "toxi" / ".codex-plugin" / "plugin.json").write_text("{}")
    nested.mkdir()

    monkeypatch.setattr("toxi.bootstrap.repo_root", lambda: installed)
    monkeypatch.chdir(nested)

    assert bootstrap.codex_repo_root() == checkout
    assert bootstrap.codex_marketplace_path() == checkout / ".agents" / "plugins" / "marketplace.json"


@pytest.fixture(autouse=True)
def _codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))


def test_setup_codex_installs_read_prompt_once(monkeypatch, tmp_path):
    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "present")
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: None)
    prompt = tmp_path / "codex-home" / "prompts" / "toxi-read.md"

    first = CliRunner().invoke(cli, ["setup-codex"])
    assert "/prompts:toxi-read installed" in first.output
    text = prompt.read_text()
    assert "get_unread" in text and "mark_read" in text and "$ARGUMENTS" in text
    assert "toxi unread" not in text  # Codex's shell lacks the MCP server's TOXI_HOME

    again = CliRunner().invoke(cli, ["setup-codex"])
    assert "/prompts:toxi-read already installed" in again.output

    CliRunner().invoke(cli, ["teardown-codex"])
    assert not prompt.exists()


def test_setup_codex_when_codex_missing(monkeypatch):
    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "present")
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: None)

    res = CliRunner().invoke(cli, ["setup-codex"])

    assert res.exit_code == 0
    assert "MCP extra already installed" in res.output
    assert "Codex CLI not found" in res.output
    assert "codex mcp add toxi -- toxi mcp serve" in res.output


def test_setup_codex_registers_mcp_and_plugin(monkeypatch):
    calls = []

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(["codex", *args], 0, stdout="ok", stderr="")

    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "present")
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)

    res = CliRunner().invoke(cli, ["setup-codex"])

    assert res.exit_code == 0
    assert ["mcp", "add", "toxi", "--", "toxi", "mcp", "serve"] in calls
    assert ["plugin", "marketplace", "add", str(bootstrap.repo_root())] in calls
    assert ["plugin", "add", "toxi@toxi"] in calls
    assert "Installed Codex plugin `toxi`" in res.output


def test_setup_codex_without_source_checkout_registers_only_mcp(monkeypatch, tmp_path):
    calls = []

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(["codex", *args], 0, stdout="ok", stderr="")

    missing = tmp_path / ".agents" / "plugins" / "marketplace.json"
    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "present")
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)
    monkeypatch.setattr("toxi.cli.bootstrap.codex_marketplace_path", lambda: missing)

    res = CliRunner().invoke(cli, ["setup-codex"])

    assert res.exit_code == 0
    assert ["mcp", "add", "toxi", "--", "toxi", "mcp", "serve"] in calls
    assert not any(args[:3] == ["plugin", "marketplace", "add"] for args in calls)
    assert not any(args[:2] == ["plugin", "add"] for args in calls)
    assert "plugin install currently requires a source checkout" in res.output


def test_setup_codex_reports_codex_command_failure(monkeypatch):
    def fake_run(args):
        return subprocess.CompletedProcess(["codex", *args], 1, stdout="", stderr="boom")

    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "failed")
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)

    res = CliRunner().invoke(cli, ["setup-codex"])

    assert res.exit_code == 0
    assert "Could not install the MCP extra" in res.output
    assert "Could not register Codex MCP server" in res.output
    assert "boom" in res.output


def test_setup_codex_treats_already_registered_as_success(monkeypatch):
    def fake_run(args):
        return subprocess.CompletedProcess(["codex", *args], 1, stdout="", stderr="already exists")

    monkeypatch.setattr("toxi.cli._ensure_mcp_extra", lambda: "present")
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)

    res = CliRunner().invoke(cli, ["setup-codex"])

    assert res.exit_code == 0
    assert "already registered" in res.output
    assert "already installed" in res.output


def test_teardown_codex_when_codex_missing(monkeypatch):
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: None)

    res = CliRunner().invoke(cli, ["teardown-codex"])

    assert res.exit_code == 0
    assert "Codex CLI not found" in res.output
    assert "codex plugin remove toxi@toxi" in res.output


def test_teardown_codex_removes_plugin_mcp_and_marketplace(monkeypatch):
    calls = []

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(["codex", *args], 0, stdout="ok", stderr="")

    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)

    res = CliRunner().invoke(cli, ["teardown-codex"])

    assert res.exit_code == 0
    assert ["plugin", "remove", "toxi@toxi"] in calls
    assert ["mcp", "remove", "toxi"] in calls
    assert ["plugin", "marketplace", "remove", "toxi"] in calls
    assert "Identity + history preserved" in res.output


def test_teardown_codex_treats_missing_entries_as_success(monkeypatch):
    def fake_run(args):
        return subprocess.CompletedProcess(["codex", *args], 1, stdout="", stderr="not found")

    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)

    res = CliRunner().invoke(cli, ["teardown-codex"])

    assert res.exit_code == 0
    assert "already absent" in res.output


def test_teardown_codex_reports_command_failure(monkeypatch):
    def fake_run(args):
        return subprocess.CompletedProcess(["codex", *args], 1, stdout="", stderr="boom")

    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._run_codex", fake_run)

    res = CliRunner().invoke(cli, ["teardown-codex"])

    assert res.exit_code == 0
    assert "Could not remove Codex plugin" in res.output
    assert "boom" in res.output


def test_doctor_codex_reports_missing_codex(monkeypatch):
    monkeypatch.setattr("toxi.cli._mcp_extra_present", lambda: True)
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: None)

    res = CliRunner().invoke(cli, ["doctor-codex"])

    assert res.exit_code != 0
    assert "Codex CLI not found" in res.output
    assert "Codex integration incomplete" in res.output


def test_doctor_codex_passes_when_wiring_is_present(monkeypatch):
    def fake_stdout(args):
        data = {
            ("mcp", "list"): "Name  Command\ntoxi  toxi mcp serve",
            ("plugin", "marketplace", "list"): "MARKETPLACE  ROOT\ntoxi  /repo",
            ("plugin", "list"): "PLUGIN      STATUS     VERSION  PATH\ntoxi@toxi   installed  0.2.5   /repo/plugins/toxi",
        }
        return True, data[tuple(args)]

    monkeypatch.setattr("toxi.cli._mcp_extra_present", lambda: True)
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._codex_stdout", fake_stdout)

    res = CliRunner().invoke(cli, ["doctor-codex"])

    assert res.exit_code == 0
    assert "Codex integration looks ready" in res.output


def test_doctor_codex_reports_missing_wiring(monkeypatch):
    def fake_stdout(args):
        data = {
            ("mcp", "list"): "No MCP servers configured yet.",
            ("plugin", "marketplace", "list"): "MARKETPLACE  ROOT\nopenai-curated  /tmp/plugins",
            ("plugin", "list"): "PLUGIN      STATUS\ntoxi@toxi   not installed",
        }
        return True, data[tuple(args)]

    monkeypatch.setattr("toxi.cli._mcp_extra_present", lambda: False)
    monkeypatch.setattr("toxi.cli.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("toxi.cli._codex_stdout", fake_stdout)

    res = CliRunner().invoke(cli, ["doctor-codex"])

    assert res.exit_code != 0
    assert "MCP extra is not importable" in res.output
    assert "Codex MCP server `toxi` is not registered" in res.output
    assert "Codex plugin marketplace `toxi` is not registered" in res.output
    assert "Codex plugin `toxi` is not installed" in res.output
