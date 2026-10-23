"""Filesystem layout for toxi (see PRD §4.8).

The config dir defaults to ~/.config/toxi/ but can be overridden with the
TOXI_HOME env var — this is what lets us run two independent daemons on
one machine for testing.
"""
import os
from pathlib import Path

ENV_HOME = "TOXI_HOME"


def config_dir() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override)
    return Path.home() / ".config" / "toxi"


def ensure_config_dir() -> Path:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return config_dir() / "chat.db"


def socket_path() -> Path:
    return config_dir() / "daemon.sock"


def pid_path() -> Path:
    return config_dir() / "daemon.pid"


def lock_path() -> Path:
    return config_dir() / "daemon.lock"


def log_path() -> Path:
    return config_dir() / "daemon.log"


def config_path() -> Path:
    return config_dir() / "config.toml"


def tox_state_path() -> Path:
    return config_dir() / "tox_state.bin"


def wrapped_statusline_path() -> Path:
    # The user's pre-existing statusLine command, saved when toxi wraps it.
    return config_dir() / "statusline_wrapped"


def media_dir() -> Path:
    return config_dir() / "media"


def ensure_media_dir() -> Path:
    d = media_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def bootstrap_path() -> Path:
    return config_dir() / "bootstrap.json"


def inbox_flag_path() -> Path:
    # Touched by the daemon whenever an incoming message is stored: hooks stat it
    # and only talk to the daemon when its mtime moved (design §4.3).
    return config_dir() / "inbox.flag"


def profile_path() -> Path:
    # This identity's self-described profile card (design §4.1), sent to friends.
    return config_dir() / "profile.json"
