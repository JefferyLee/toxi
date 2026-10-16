"""`toxi service`: keep the daemon alive across logins and reboots (design §4.7).

macOS: a launchd user agent (~/Library/LaunchAgents/<label>.plist, KeepAlive).
Linux: a systemd user unit (~/.config/systemd/user/<name>.service, Restart=always).
One service per identity: when TOXI_HOME is not the default directory the label
carries a tag derived from it, and the service pins TOXI_HOME explicitly.
"""
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

LABEL_BASE = "dev.toxi.daemon"


def tag() -> str | None:
    home = paths.config_dir()
    default = Path.home() / ".config" / "toxi"
    if home.expanduser().resolve() == default.resolve():
        return None
    return re.sub(r"[^A-Za-z0-9]+", "-", home.name).strip("-") or "alt"


def label() -> str:
    t = tag()
    return f"{LABEL_BASE}.{t}" if t else LABEL_BASE


def unit_name() -> str:
    t = tag()
    return f"toxi-daemon-{t}" if t else "toxi-daemon"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label()}.plist"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{unit_name()}.service"


def service_path() -> Path:
    return plist_path() if is_macos() else unit_path()


def manager() -> str | None:
    """Path of the service manager CLI (launchctl / systemctl), or None if absent."""
    return shutil.which("launchctl" if is_macos() else "systemctl")


def installed() -> bool:
    # A definition file without a manager to run it (e.g. a Linux box without
    # systemd) does not count: nothing would restart the daemon.
    return service_path().exists() and manager() is not None


def _program() -> list[str]:
    # The `toxi-daemon` script next to the interpreter this toxi runs under
    # (pipx/uv/venv), so the service uses the same installation as the CLI and
    # shows up as "toxi-daemon", not "python", in the OS's login-items list.
    script = Path(sys.executable).with_name("toxi-daemon")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "toxi.daemon"]


def _env() -> dict:
    return {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "TOXI_HOME": str(paths.config_dir())}


def render_plist() -> bytes:
    log = str(paths.config_dir() / "service.log")
    return plistlib.dumps({
        "Label": label(),
        "ProgramArguments": _program(),
        "EnvironmentVariables": _env(),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    })


def render_unit() -> str:
    env = " ".join(f'"{k}={v}"' for k, v in _env().items())
    return (
        "[Unit]\n"
        f"Description=toxi daemon ({paths.config_dir()})\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={' '.join(_program())}\n"
        f"Environment={env}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


class ServiceError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: command not found")


def _check(r: subprocess.CompletedProcess, what: str) -> None:
    if r.returncode:
        raise ServiceError(f"{what} failed: {(r.stderr or r.stdout).strip() or r.returncode}")


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> Path:
    """Write the service definition and start it. Idempotent.

    Probes for the service manager first and removes the definition again if
    activation fails, so a failed install never leaves a file that would make
    `installed()` lie later (e.g. a Linux VM without systemd).
    """
    if manager() is None:
        raise ServiceError("no service manager here: " + (
            "launchctl not found" if is_macos()
            else "systemctl not found (no systemd user session on this machine); "
                 "keep starting the daemon with `toxi daemon start` (e.g. from cron)"))
    p = service_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    try:
        if is_macos():
            if existed:
                _run(["launchctl", "bootout", f"{_domain()}/{label()}"])
            p.write_bytes(render_plist())
            _check(_run(["launchctl", "bootstrap", _domain(), str(p)]), "launchctl bootstrap")
        else:
            p.write_text(render_unit())
            _check(_run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
            _check(_run(["systemctl", "--user", "enable", "--now", unit_name()]), "systemctl enable")
    except ServiceError:
        if not existed:
            p.unlink(missing_ok=True)
        raise
    return p


def uninstall() -> bool:
    """Stop the service and remove its definition (also a stale one left by a
    failed install). Returns False if there was nothing to remove."""
    p = service_path()
    if not p.exists():
        return False
    if manager() is not None:
        if is_macos():
            _run(["launchctl", "bootout", f"{_domain()}/{label()}"])
        else:
            _run(["systemctl", "--user", "disable", "--now", unit_name()])
    p.unlink()
    if not is_macos() and manager() is not None:
        _run(["systemctl", "--user", "daemon-reload"])
    return True


def restart() -> None:
    if is_macos():
        _check(_run(["launchctl", "kickstart", "-k", f"{_domain()}/{label()}"]), "launchctl kickstart")
    else:
        _check(_run(["systemctl", "--user", "restart", unit_name()]), "systemctl restart")


def status() -> dict:
    """{"installed", "running", "pid", "path"} as the service manager sees it."""
    info = {"installed": installed(), "running": False, "pid": None, "path": str(service_path())}
    if not info["installed"]:
        return info
    if is_macos():
        r = _run(["launchctl", "print", f"{_domain()}/{label()}"])
        if r.returncode == 0:
            m = re.search(r"^\s*pid = (\d+)", r.stdout, re.M)
            info["running"] = bool(m) or "state = running" in r.stdout
            info["pid"] = int(m.group(1)) if m else None
    else:
        r = _run(["systemctl", "--user", "show", unit_name(), "-p", "ActiveState", "-p", "MainPID"])
        info["running"] = "ActiveState=active" in r.stdout
        m = re.search(r"MainPID=(\d+)", r.stdout)
        info["pid"] = int(m.group(1)) if m and m.group(1) != "0" else None
    return info
