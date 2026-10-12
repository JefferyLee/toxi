#!/usr/bin/env python3
"""Codex Stop hook for toxi.

If a peer agent (config.toml [agents] peers) has messaged us, blocks the stop
and feeds those messages back to Codex so it keeps working on them. Otherwise
emits Codex Stop hook JSON with the current `toxi statusline` summary.

Hooks don't see the MCP server's env, so when TOXI_HOME isn't set we reuse the
one configured for the toxi MCP server in Codex's config.toml.
"""
import json
import os
import subprocess
from pathlib import Path


def codex_toxi_home() -> str | None:
    """TOXI_HOME from [mcp_servers.toxi.env] in Codex's config.toml, if any."""
    try:
        import tomllib
        cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        with open(cfg, "rb") as f:
            return tomllib.load(f)["mcp_servers"]["toxi"]["env"]["TOXI_HOME"]
    except Exception:
        return None


def run(toxi: str, *args: str) -> str:
    try:
        out = subprocess.run(
            [toxi, *args], capture_output=True, text=True, timeout=8
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def main() -> None:
    if "TOXI_HOME" not in os.environ:
        home = codex_toxi_home()
        if home:
            os.environ["TOXI_HOME"] = os.path.expanduser(home)
    toxi = os.environ.get("TOXI_BIN", "toxi")

    peer = run(toxi, "peer-inbox")
    if peer:
        print(json.dumps({"decision": "block", "reason": peer}))
        return

    status = run(toxi, "statusline")
    if status:
        print(
            json.dumps(
                {
                    "continue": True,
                    "suppressOutput": True,
                    "systemMessage": status,
                }
            )
        )


if __name__ == "__main__":
    main()
