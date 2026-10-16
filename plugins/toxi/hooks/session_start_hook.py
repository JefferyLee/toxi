#!/usr/bin/env python3
"""Codex SessionStart hook for toxi: deliver peer-agent messages routed to this
project as plain stdout (Codex adds it as developer context), so a session that
starts or resumes picks up where the peers left off.

Set TOXI_BIN if `toxi` isn't on PATH; TOXI_HOME is taken from Codex's own
[mcp_servers.toxi] env when not set, like the Stop hook does.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def codex_toxi_home():
    try:
        import tomllib
        cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        with open(cfg, "rb") as f:
            return tomllib.load(f)["mcp_servers"]["toxi"]["env"]["TOXI_HOME"]
    except Exception:
        return None


def main() -> None:
    if "TOXI_HOME" not in os.environ:
        home = codex_toxi_home()
        if home:
            os.environ["TOXI_HOME"] = os.path.expanduser(home)
    toxi = os.environ.get("TOXI_BIN", "toxi")
    cwd = None
    if not sys.stdin.isatty():
        try:
            cwd = json.load(sys.stdin).get("cwd")
        except (ValueError, AttributeError):
            pass
    cwd = cwd or os.getcwd()
    try:
        out = subprocess.run([toxi, "peer-inbox", "--cwd", cwd], capture_output=True, text=True, timeout=8)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    text = out.stdout.strip() if out.returncode == 0 else ""
    if text:
        print(text)


if __name__ == "__main__":
    main()
    sys.exit(0)
