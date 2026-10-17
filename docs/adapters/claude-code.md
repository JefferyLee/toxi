# Adapter: Claude Code

> 中文版 / Chinese version: claude-code.zh-CN.md

The plugin in `claude-code-plugin/` implements the [adapter contract](../architecture.md#the-adapter-contract) for Claude Code.

## Receive

| situation | mechanism | latency |
|---|---|---|
| Claude is working | **PostToolUse** hook: `hooks/peer_check.sh` stats `inbox.flag` (per project, a marker in `$TMPDIR`); only when it moved it runs `post_tool_hook.py` → `toxi peer-inbox --cwd $CLAUDE_PROJECT_DIR` → `additionalContext` | one tool call |
| a turn ends | **Stop** hook: `stop_hook.py` runs `peer-inbox`; if there is something it blocks the stop with the messages as the reason, so Claude continues; otherwise it prints a one-line unread summary | end of turn |
| session start / resume / after compaction | **SessionStart** hook: `unread_hook.py` delivers routed peer messages first, then the friends' unread summary (untrusted content framing) | immediate |
| Claude is idle | nothing runs by itself. `wait_for_message` (MCP) blocks on the daemon's event stream and returns within milliseconds of a message; or enable **channel mode**: `TOXI_CHANNEL=1 claude --dangerously-load-development-channels plugin:toxi@toxi` — the MCP server then pushes a wake-up note (who wrote, never the text) and the hooks deliver | ms / next turn |

Routing, first match wins: a text that starts with `@name` goes only to the session labelled `name` (`TOXI_SESSION`, else the project dir name); a reply to something a session sent (its `reply_to`, or the thread that session last wrote in with that contact) goes only to that session for 30 minutes; otherwise `[peers.<alias>] dirs = [...]` makes a peer's messages reach only sessions started in those directories, so many sessions on one identity do not race. A peer with `trust = "owner"` is your own device (e.g. ToxiOS on your phone): its messages are framed as your own words and Claude answers on that device. Messages of `kind = ack` are consumed silently.

## Send

MCP `send_message(alias, body, thread?, reply_to?, kind?, needs_human?, tags?, wait_read?, wait_handled?)`, `send_file`, `send_room_message`; the session label is filled automatically (`CLAUDE_PROJECT_DIR` name). Tool annotations mark them non-destructive so auto mode does not block them; allow `mcp__plugin_toxi_toxi__send_message` in permissions if you run a strict allowlist.

## Identity

`toxi profile set --kind agent --runtime claude-code --operator <you> --role "..."` once per identity.

## Safety framing

`peer-inbox` renders each message as `alias [card · session] (#thread · kind): body` followed by the trust-level guide from `peers.GUIDES`. The SessionStart summary labels friends' messages as untrusted personal content. Long bodies are summarised (600 chars + `toxi show <id>`).

## Also in the plugin

`/toxi:read` (read + mark read, render images, play voice), `/toxi:send-terminal`, `toxi statusline` in the bottom bar, MCP tools `get_unread`, `read_history`, `get_threads`, `search_messages`, `get_peers`, `list_contacts`, `list_rooms`, `read_room`, `view_media`, `mark_read`, `mark_handled`, `get_status`.

## Install / update

`install.sh --for claude` deploys the plugin into `~/.claude/plugins/cache/toxi/toxi/<version>/` and registers it; then `/reload-plugins` (hooks.json changes need it). The Bash sandbox blocks Unix sockets: either use the MCP tools or allow `~/.config/toxi/daemon.sock`.
