#!/bin/sh
# PostToolUse pre-check for toxi (design §4.3): only start python and talk to the
# daemon when the daemon's inbox.flag moved since this project last looked, so
# the per-tool-call cost is a stat, not a process. Hook input stays on stdin.
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
