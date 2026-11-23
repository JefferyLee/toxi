# Adapter: Codex

> 中文版 / Chinese version: codex.zh-CN.md
> For day-to-day usage, see [../codex-usage.md](../codex-usage.md).

`plugins/toxi/` connects Codex to toxi. It has these parts:

- `.mcp.json` starts `toxi mcp serve`.
- `hooks/hooks.json` registers the SessionStart, PostToolUse and Stop hooks.
- `skills/toxi/SKILL.md` teaches Codex the rules.

## Receive

Codex CLI 0.161 and later runs the same hook events as Claude Code. Thus the plugin delivers messages in the same way:

- **While working**: After a tool call, a PostToolUse hook injects routed peer messages as `additionalContext`. The hook is `hooks/peer_check.sh`. It does a stat of `inbox.flag`, then runs `toxi peer-inbox --peek`. It marks the messages read after it printed them.
- **Session start / resume**: `hooks/session_start_hook.py` prints the routed peer inbox as developer context.
- **End of turn**: The Stop hook runs `toxi peer-inbox --peek --cwd <session dir>`, so it takes only the messages of this session. If there are messages, it blocks the stop with the messages. If not, it sends the `toxi statusline` summary as a system message.
- **Idle**: Codex has no idle event. But the Codex CLI (0.167 and later) runs its sessions in a shared app-server daemon. The daemon's control socket, `$CODEX_HOME/app-server-control/app-server-control.sock`, speaks JSON-RPC over WebSocket. The SessionStart and Stop hooks start `toxi codex-wake`, one for each toxi identity. When mail for a session should wake it (the `[wake] kinds`, owner messages, needs-human messages and answers) and its thread is idle, the waker adds the mail to the thread as a developer message (`thread/inject_items`). Then it starts a turn with no user input (`turn/start`). Thus the peer's words never look like the user's words. Other mail stays unread until the next run. The waker wakes one thread 20 times an hour at most, so two agents cannot wake each other for ever.
- **Sessions in the daemon**: the daemon runs all the threads, and it starts the hooks and the MCP servers. Thus toxi registers each session by its thread id: the hooks send `session_id`, and the MCP server reads `threadId` from the `_meta` of each tool call. The waker renews these entries while the daemon has the thread loaded, and ends an entry when the daemon unloads the thread. A Codex with `--no-daemon`, and the ChatGPT desktop app, run their threads in a private server that toxi cannot reach. For them, nothing wakes an idle session, and the process is the session, as before. There, Codex calls `wait_for_message` and loops while it waits for a peer.
- **Approvals**: a woken turn follows the approval policy of the session. If `send_message` needs approval, the turn waits for you in the TUI.

If `TOXI_HOME` is not in the environment of the hooks, they read it from `[mcp_servers.toxi] env` in `~/.codex/config.toml`. In Codex, use `/hooks` once to review and trust the hooks.

The hook commands find the plugin through `PLUGIN_ROOT`, which Codex sets. If a script is missing, the hook command exits 0. The reason is that Codex reads exit 2 from a Stop hook as "do not stop". If an upgrade changes `hooks.json`, Codex shows the hooks as untrusted again. In this case, trust the hooks in `/hooks`. Then restart the session.

**Read on demand**: `toxi setup codex` installs the custom prompt `/prompts:toxi-read [alias]`. This prompt reads unread messages through the MCP tools and marks them as read. It does not run `toxi unread` in the shell, because the shell of Codex does not have the `TOXI_HOME` of the MCP server.

## Send / identity / safety

Codex uses the same MCP tools as Claude Code. Usually, Codex runs its own identity. This identity uses `TOXI_HOME=~/.config/toxi-codex` under `[mcp_servers.toxi] env` in `~/.codex/config.toml`. The hook also reads that env. To set the card of Codex, run `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>`. The skill repeats the untrusted-input boundary and the trust levels.

## Install

Run `toxi setup codex`. From a checkout, it registers the MCP server, the repo marketplace and the plugin. `toxi doctor` reports the state. `toxi teardown-codex` removes the setup.
