"""Helpers for `toxi setup` / `teardown` / `upgrade`.

Pure helpers — no Click, no side effects on import. Filesystem touchers return
small status strings so the CLI can phrase its own output.
"""
import json
import os
import shutil
from pathlib import Path

from . import client, ipc, paths

STATUSLINE_CMD = "toxi statusline"

# Claude Code keeps installed plugins under ~/.claude/plugins/. The marketplace
# name comes from .claude-plugin/marketplace.json (we declare it as "toxi") and
# the plugin name from claude-code-plugin/.claude-plugin/plugin.json (also "toxi").
PLUGIN_NAME = "toxi"
MARKETPLACE_NAME = "toxi"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _codex_checkout_root_from_cwd() -> Path | None:
    """Return the nearest source checkout root when running inside one."""
    start = Path.cwd().resolve()
    for p in (start, *start.parents):
        if (
            (p / ".agents" / "plugins" / "marketplace.json").exists()
            and (p / "plugins" / PLUGIN_NAME / ".codex-plugin" / "plugin.json").exists()
        ):
            return p
    return None


def codex_repo_root() -> Path:
    """Root to use for Codex plugin marketplace commands.

    A pipx/Homebrew-installed `toxi` command may run while the user is standing
    in a source checkout. In that case the Codex plugin files live in the
    checkout, not next to the installed Python package.
    """
    return _codex_checkout_root_from_cwd() or repo_root()


def codex_marketplace_path() -> Path:
    return codex_repo_root() / ".agents" / "plugins" / "marketplace.json"


def claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def claude_plugins_dir() -> Path:
    return Path.home() / ".claude" / "plugins"


def toxi_marketplace_clone() -> Path:
    return claude_plugins_dir() / "marketplaces" / MARKETPLACE_NAME


def toxi_plugin_cache_root() -> Path:
    # Claude Code convention: cache/<marketplace>/<plugin>/<version>/
    return claude_plugins_dir() / "cache" / MARKETPLACE_NAME / PLUGIN_NAME


def installed_plugins_path() -> Path:
    return claude_plugins_dir() / "installed_plugins.json"


def _read_settings(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _write_settings(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_suffix(".json.bak"))
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, p)


def wrapped_statusline_command() -> str | None:
    """The user's pre-existing statusLine command that toxi wraps, if any."""
    wp = paths.wrapped_statusline_path()
    if not wp.exists():
        return None
    cmd = wp.read_text().strip()
    return cmd or None


def ensure_statusline() -> str:
    """Wire toxi into ~/.claude/settings.json.

    If the user already runs their own command-type statusLine, toxi wraps it:
    it takes over the statusLine slot and `toxi statusline` re-runs the original
    and appends its own segment. Returns:
    "added" | "kept" | "wrapped" | "left-custom".
    """
    p = claude_settings_path()
    data = _read_settings(p)
    sl = data.get("statusLine")
    wp = paths.wrapped_statusline_path()
    if isinstance(sl, dict) and sl.get("command") == STATUSLINE_CMD:
        return "kept"
    if isinstance(sl, dict) and sl.get("type") == "command" and isinstance(sl.get("command"), str):
        wp.parent.mkdir(parents=True, exist_ok=True)
        wp.write_text(sl["command"])
        data["statusLine"] = {"type": "command", "command": STATUSLINE_CMD}
        _write_settings(p, data)
        return "wrapped"
    if sl is not None:
        return "left-custom"  # a non-command statusLine we can't re-run — don't touch it
    if wp.exists():
        wp.unlink()  # clear any stale wrapped command from a prior install
    data["statusLine"] = {"type": "command", "command": STATUSLINE_CMD}
    _write_settings(p, data)
    return "added"


def remove_statusline() -> str:
    """Reverse of ensure_statusline. If toxi had wrapped a prior command, restore it.

    Returns: "removed" | "restored" | "absent" | "left-custom".
    """
    p = claude_settings_path()
    data = _read_settings(p)
    sl = data.get("statusLine")
    wp = paths.wrapped_statusline_path()
    if sl is None:
        if wp.exists():
            wp.unlink()
        return "absent"
    if not (isinstance(sl, dict) and sl.get("command") == STATUSLINE_CMD):
        return "left-custom"
    if wp.exists():
        prev = wp.read_text().strip()
        wp.unlink()
        if prev:
            data["statusLine"] = {"type": "command", "command": prev}
            _write_settings(p, data)
            return "restored"
    del data["statusLine"]
    _write_settings(p, data)
    return "removed"


def identity_initialized() -> bool:
    return paths.tox_state_path().exists()


def daemon_running() -> bool:
    try:
        client.request("get_status")
        return True
    except client.DaemonNotRunning:
        return False
    except (client.DaemonError, ipc.IPCError, OSError):
        return False  # answering but broken, or shutting down mid-handshake: not usable
