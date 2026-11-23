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


TRUST_LEVELS = ("observer", "collaborator", "owner")


def peer_config() -> dict[str, dict]:
    """Peers by alias: {"dirs": [...], "trust": "observer" | "collaborator" | "owner"}.

    Read from `[peers.<alias>]` (`dirs = [...]`, `trust = "..."`, optional
    `key = "<64 hex public key>"`: only that key may get this alias). The legacy
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
                      "trust": trust if trust in TRUST_LEVELS else "observer",
                      "key": str(spec.get("key") or "").strip().upper()[:64] or None}
    return out


WAKE_KINDS = ("msg", "request", "relay")


def wake_kinds() -> tuple:
    """`[wake] kinds = [...]`: the kinds of peer text that wake an idle session
    (design §13.9). Messages from an `owner`, decisions for the user and answers
    to this session always wake; an `ack` never does."""
    kinds = _load().get("wake", {}).get("kinds")
    return tuple(str(k) for k in kinds) if isinstance(kinds, list) else WAKE_KINDS


def send_dirs() -> list[str]:
    """`[files] send_dirs = [...]`: more dirs whose files may be sent, besides a
    running session's project dir, the temp dir and toxi's media dir."""
    d = _load().get("files", {}).get("send_dirs", [])
    return [str(x) for x in ([d] if isinstance(d, str) else d)]


ARCHIVE_AFTER_DAYS = 90


def archive_after_days() -> int:
    """`[archive] after_days = N`: the daemon moves messages older than N days to
    archive.db once a day (default 90; 0 = never). Undelivered ones stay."""
    try:
        return max(0, int(_load().get("archive", {}).get("after_days", ARCHIVE_AFTER_DAYS)))
    except (TypeError, ValueError):
        return ARCHIVE_AFTER_DAYS


def tox_config() -> dict:
    """`[tox]`: extra DHT bootstrap nodes and whether to use the public ones.

        [tox]
        public_dht = true                       # false = only the nodes below
        bootstrap = [{ host = "127.0.0.1", port = 33447, key = "<64 hex DHT id>" }]

    Lets two daemons on one LAN (or one machine) find each other directly —
    `toxi me` prints a node's DHT id and UDP port.
    """
    t = _load().get("tox", {})
    nodes = []
    for n in t.get("bootstrap", []) or []:
        if isinstance(n, dict) and n.get("host") and n.get("port") and n.get("key"):
            nodes.append((str(n["host"]), int(n["port"]), str(n["key"])))
        elif isinstance(n, (list, tuple)) and len(n) == 3:
            nodes.append((str(n[0]), int(n[1]), str(n[2])))
    return {"bootstrap": nodes, "public_dht": bool(t.get("public_dht", True))}


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
