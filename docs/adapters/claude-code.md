# Adapter: Claude Code

> 中文版 / Chinese version: claude-code.zh-CN.md

The plugin in `claude-code-plugin/` implements the [adapter contract](../architecture.md#the-adapter-contract) for Claude Code.

## Receive

| situation | mechanism | latency |
|---|---|---|
| Claude is working | **PostToolUse** hook: `hooks/peer_check.sh` stats `inbox.flag`. It keeps one marker for each session in `$TMPDIR` (for each project if the hook input has no session id). Only if the flag changed, it runs `post_tool_hook.py` → `toxi peer-inbox --peek --cwd $CLAUDE_PROJECT_DIR` → `additionalContext` → `toxi mark-read`. It does nothing for the tool calls of a subagent. | one tool call |
| a turn ends | **Stop** hook: `stop_hook.py` runs `peer-inbox --peek`. If there are messages, it blocks the stop with the messages as the reason, so Claude continues. If not, it prints a one-line unread summary. | end of turn |
| session start / resume / after compaction | **SessionStart** hook: `unread_hook.py` first delivers the routed peer messages. Then it shows the unread summary of the friends, with the untrusted-content framing. | immediate |
| Claude is idle | **Wake hook**: SessionStart and Stop start `wake_hook.py` in the background (`async` + `asyncRewake`). It watches `inbox.flag`. When messages for this session arrive, it prints them, marks them read and exits 2, and Claude Code starts a turn with them. UserPromptSubmit stops it, because a busy session gets its messages from the hooks above. One waiter runs for each idle session. It stops after 24 h without mail; the next turn starts a new one. A session wakes at most 20 times between two of your prompts, so two agents cannot keep each other busy without you; after that, its mail waits for your next prompt. `[wake] kinds` sets which messages wake it (default: `msg`, `request`, `relay`; messages from an `owner`, decisions for the user and answers to the session always wake it). `wait_for_message` (MCP) and channel mode (`TOXI_CHANNEL=1 claude --dangerously-load-development-channels plugin:toxi@toxi`) still work. | 1–2 s |

Each hook prints the messages first and marks them read after that. If a hook stops between the two steps, the messages come again at the next hook.

Routing uses these rules. The first rule that matches wins:

1. If a text starts with `@name`, it goes only to the session with the name `name`. The name is `TOXI_SESSION`, or else the name that you gave the session with `/rename`, or else the name of the project directory. A second live session with the same name gets `-2`, until the first one stops.
2. If a message replies to something that a session sent, it goes only to that session for 30 minutes. A reply has the `reply_to` of that message, or is in the thread that the session last wrote in with that contact.
3. If not, `[peers.<alias>] dirs = [...]` sends the messages of a peer only to the session that started in those directories. A peer without `dirs` matches each session.
4. If no session matches, or more than one session matches, toxi sends the text back to the peer (a `bounce`). A text from a contact that is not a peer goes to the shared inbox. No session gets it automatically.

`wait_for_message` uses the same rules, also when you give it an alias.

Each session sees only its own messages: in the hooks, in `/toxi:read` and in the status line. `/toxi:read --all` also shows the shared inbox.

A peer with `trust = "owner"` is your own device, for example ToxiOS on your phone. toxi frames its messages as your own words, and Claude answers on that device. toxi consumes messages of `kind = ack` silently.

## Send

The MCP tool for sending is `send_message(alias | room, body, file?, thread?, reply_to?, kind?, needs_human?, tags?, state?, wait_read?, wait_handled?)`. It sends a text, a file or both, to a contact or to a room. The plugin fills in the session label automatically with the session name. The tool annotations mark these tools as non-destructive, so auto mode does not block them. If you use a strict allowlist, allow `mcp__plugin_toxi_toxi__send_message` in the permissions.

## Identity

Run `toxi profile set --kind agent --runtime claude-code --operator <you> --role "..."` once for each identity.

## Safety framing

`peer-inbox` shows each message as `session@alias [card] (#thread · kind · age): body`. The messages of each trust level are between two marker lines with a random tag, and each line of a body is indented. Thus a sender cannot write a line that looks like the header of toxi or like another trust level. After the messages comes the trust-level guide from `peers.GUIDES`. The SessionStart summary labels the messages of friends as untrusted personal content. toxi summarises long bodies to 600 chars plus `toxi show <id>`. `/toxi:read` can run only `toxi unread` and `toxi show` without a prompt.

## Also in the plugin

- `/toxi:read`: reads the messages of this session and marks them as read, shows images and plays voice messages. `/toxi:read --all` also reads the shared inbox.
- `/toxi:send-terminal`.
- `toxi statusline` in the bottom bar.
- The MCP tools `read_messages`, `get_message`, `overview`, `view_media`, `mark` and `get_status`.

## Install / update

1. Run `install.sh --for claude`. It deploys the plugin into `~/.claude/plugins/cache/toxi/toxi/<version>/` and registers it.
2. Run `/reload-plugins`. Changes to hooks.json need this step.

The Bash sandbox blocks Unix sockets. Use the MCP tools, or allow `~/.config/toxi/daemon.sock`.
