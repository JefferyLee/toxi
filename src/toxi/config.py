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


TRUST_LEVELS = ("observer", "collaborator")


def peer_config() -> dict[str, dict]:
    """Peer agents by alias: {"dirs": [...], "trust": "observer" | "collaborator"}.

    Read from `[peers.<alias>]` (`dirs = [...]`, `trust = "..."`). The legacy
    `[agents] peers = [...]` + `[agents.dirs]` form is still honoured and means
    trust = "collaborator". An unknown trust level degrades to "observer".
    """
    cfg = _load()
    out: dict[str, dict] = {}
    agents = cfg.get("agents", {})
    legacy_dirs = agents.get("dirs", {})
    for alias in agents.get("peers", []):
        d = legacy_dirs.get(alias)
        out[alias] = {"dirs": [d] if d else [], "trust": "collaborator"}
    for alias, spec in cfg.get("peers", {}).items():
        if not isinstance(spec, dict):
            continue
        dirs = spec.get("dirs", [])
        if isinstance(dirs, str):
            dirs = [dirs]
        trust = spec.get("trust", "collaborator")
        out[alias] = {"dirs": [str(d) for d in dirs],
                      "trust": trust if trust in TRUST_LEVELS else "observer"}
    return out


def agent_peers() -> list[str]:
    """Aliases of contacts that are peer AI agents."""
    return list(peer_config())


def on_message_hook() -> str | None:
    """Shell command run when new messages arrive (`[hooks] on_message`)."""
    return _load().get("hooks", {}).get("on_message") or None


def webhook_config() -> dict | None:
    """`[hooks] on_message_url` (+ `on_message_secret`, `include_body`): an HTTP
    POST per batch of new messages, HMAC-signed, bodies only when opted in."""
    h = _load().get("hooks", {})
    url = h.get("on_message_url")
    if not url:
        return None
    return {"url": str(url), "secret": str(h.get("on_message_secret") or ""),
            "include_body": bool(h.get("include_body", False))}


def agent_dirs() -> dict[str, list[str]]:
    """Per-peer project dirs: Stop hooks deliver that peer's messages only to
    sessions started in exactly one of those dirs."""
    return {a: spec["dirs"] for a, spec in peer_config().items() if spec["dirs"]}
