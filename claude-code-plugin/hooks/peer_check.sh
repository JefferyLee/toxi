#!/bin/sh
# PostToolUse pre-check for toxi (design §4.3): only start python and talk to the
# daemon when the daemon's inbox.flag moved since this session last looked, so
# the per-tool-call cost is a stat and a few small processes. The hook input goes
# on to python, which marks the session as having looked.
home="${TOXI_HOME:-$HOME/.config/toxi}"
flag="$home/inbox.flag"
[ -e "$flag" ] || exit 0
input=$(cat)
head=${input%%'"tool_input"'*}  # the hook's own fields, not the tool's input or output
# A subagent's tool call: leave the mail for the main session, which answers it.
case "$head" in *'"agent_id"'*) exit 0 ;; esac
# One "already looked" mark per session (else per dir), so that two sessions in
# one dir do not hide new mail from each other.
sid=$(printf '%s' "$head" | sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p')
dir="${CLAUDE_PROJECT_DIR:-$PWD}"
key=$(printf '%s' "${sid:-$dir}" | cksum | cut -d' ' -f1)
seen="${TMPDIR:-/tmp}/toxi-seen-$key"
if [ ! -e "$seen" ] || [ "$flag" -nt "$seen" ]; then
  printf '%s' "$input" | TOXI_SEEN="$seen" exec python3 "$(dirname "$0")/post_tool_hook.py"
fi
exit 0
