# Adapter: Claude Code

> 中文版 / Chinese version: claude-code.zh-CN.md

The plugin in `claude-code-plugin/` implements the [adapter contract](../architecture.md#the-adapter-contract) for Claude Code.

## Receive

| situation | mechanism | latency |
|---|---|---|
| Claude is working | **PostToolUse** hook: `hooks/peer_check.sh` stats `inbox.flag`. It keeps one marker for each project in `$TMPDIR`. Only if the flag changed, it runs `post_tool_hook.py` → `toxi peer-inbox --cwd $CLAUDE_PROJECT_DIR` → `additionalContext`. | one tool call |
| a turn ends | **Stop** hook: `stop_hook.py` runs `peer-inbox`. If there are messages, it blocks the stop with the messages as the reason, so Claude continues. If not, it prints a one-line unread summary. | end of turn |
| session start / resume / after compaction | **SessionStart** hook: `unread_hook.py` first delivers the routed peer messages. Then it shows the unread summary of the friends, with the untrusted-content framing. | immediate |
| Claude is idle | Nothing runs by itself. `wait_for_message` (MCP) blocks on the event stream of the daemon and returns within milliseconds of a message. Or enable **channel mode**: `TOXI_CHANNEL=1 claude --dangerously-load-development-channels plugin:toxi@toxi`. The MCP server then pushes a wake-up note, and the hooks deliver the messages. The note tells who wrote, but never contains the text. | ms / next turn |

Routing uses these rules. The first rule that matches wins:

1. If a text starts with `@name`, it goes only to the session with the label `name`. The label is `TOXI_SESSION`, or else the name of the project directory.
2. If a message replies to something that a session sent, it goes only to that session for 30 minutes. A reply has the `reply_to` of that message, or is in the thread that the session last wrote in with that contact.
3. If not, `[peers.<alias>] dirs = [...]` sends the messages of a peer only to sessions that started in those directories. Thus many sessions on one identity do not race.

A peer with `trust = "owner"` is your own device, for example ToxiOS on your phone. toxi frames its messages as your own words, and Claude answers on that device. toxi consumes messages of `kind = ack` silently.

## Send

The MCP tools for sending are `send_message(alias, body, thread?, reply_to?, kind?, needs_human?, tags?, wait_read?, wait_handled?)`, `send_file` and `send_room_message`. The plugin fills in the session label automatically with the name of `CLAUDE_PROJECT_DIR`. The tool annotations mark these tools as non-destructive, so auto mode does not block them. If you use a strict allowlist, allow `mcp__plugin_toxi_toxi__send_message` in the permissions.

## Identity

Run `toxi profile set --kind agent --runtime claude-code --operator <you> --role "..."` once for each identity.

## Safety framing

`peer-inbox` shows each message as `alias [card · session] (#thread · kind): body`. After the message comes the trust-level guide from `peers.GUIDES`. The SessionStart summary labels the messages of friends as untrusted personal content. toxi summarises long bodies to 600 chars plus `toxi show <id>`.

## Also in the plugin

- `/toxi:read`: reads messages and marks them as read, shows images and plays voice messages.
- `/toxi:send-terminal`.
- `toxi statusline` in the bottom bar.
- The MCP tools `get_unread`, `read_history`, `get_threads`, `search_messages`, `get_peers`, `list_contacts`, `list_rooms`, `read_room`, `view_media`, `mark_read`, `mark_handled` and `get_status`.

## Install / update

1. Run `install.sh --for claude`. It deploys the plugin into `~/.claude/plugins/cache/toxi/toxi/<version>/` and registers it.
2. Run `/reload-plugins`. Changes to hooks.json need this step.

The Bash sandbox blocks Unix sockets. Use the MCP tools, or allow `~/.config/toxi/daemon.sock`.
