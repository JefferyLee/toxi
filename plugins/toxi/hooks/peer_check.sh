#!/bin/sh
# PostToolUse pre-check for toxi (design §4.3): only start python and talk to the
# daemon when the daemon's inbox.flag moved since this project last looked, so
# the per-tool-call cost is a stat, not a process. Hook input stays on stdin.
if [ -z "${TOXI_HOME:-}" ]; then
  TOXI_HOME=$(python3 - <<'PY2' 2>/dev/null
import os, tomllib
from pathlib import Path
try:
    cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    print(os.path.expanduser(tomllib.load(open(cfg, "rb"))["mcp_servers"]["toxi"]["env"]["TOXI_HOME"]))
except Exception:
    pass
PY2
)
  [ -n "$TOXI_HOME" ] && export TOXI_HOME
fi
home="${TOXI_HOME:-$HOME/.config/toxi}"
flag="$home/inbox.flag"
[ -e "$flag" ] || exit 0
dir="${CLAUDE_PROJECT_DIR:-$PWD}"
key=$(printf '%s' "$dir" | cksum | cut -d' ' -f1)
seen="${TMPDIR:-/tmp}/toxi-seen-$key"
if [ ! -e "$seen" ] || [ "$flag" -nt "$seen" ]; then
  touch "$seen"
  exec python3 "$(dirname "$0")/post_tool_hook.py"
fi
exit 0
