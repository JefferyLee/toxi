"""User configuration from config.toml (PRD §4.8). Everything has a default, so
the file is optional.
"""
import tomllib

from . import paths

RETRY_DEFAULTS = {
    "ack_timeout_minutes": 5,
    "fail_after_hours": 24,
}


def _load() -> dict:
    p = paths.config_path()
    if p.exists():
        with open(p, "rb") as f:
            return tomllib.load(f)
    return {}


def retry_config() -> dict:
    cfg = dict(RETRY_DEFAULTS)
    cfg.update(_load().get("retry", {}))
    return cfg


def daemon_log_level() -> str:
    return _load().get("daemon", {}).get("log_level", "info")


def agent_peers() -> list[str]:
    """Aliases of contacts that are peer AI agents (`[agents] peers = [...]`)."""
    return list(_load().get("agents", {}).get("peers", []))


def on_message_hook() -> str | None:
    """Shell command run when new messages arrive (`[hooks] on_message`)."""
    return _load().get("hooks", {}).get("on_message") or None
