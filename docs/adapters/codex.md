# Adapter: Codex

> 中文版 / Chinese version: codex.zh-CN.md
> For day-to-day usage, see [../codex-usage.md](../codex-usage.md).

`plugins/toxi/` connects Codex to toxi. It has these parts:

- `.mcp.json` starts `toxi mcp serve`.
- `hooks/hooks.json` registers the SessionStart, PostToolUse and Stop hooks.
- `skills/toxi/SKILL.md` teaches Codex the rules.

## Receive

Codex CLI 0.161 and later runs the same hook events as Claude Code. Thus the plugin delivers messages in the same way:

- **While working**: After a tool call, a PostToolUse hook injects routed peer messages as `additionalContext`. The hook is `hooks/peer_check.sh`. It does a stat of `inbox.flag`, then runs `toxi peer-inbox`.
- **Session start / resume**: `hooks/session_start_hook.py` prints the routed peer inbox as developer context.
- **End of turn**: The Stop hook runs `toxi peer-inbox`. If there are messages, it blocks the stop with the messages. If not, it sends the `toxi statusline` summary as a system message.
- **Idle**: Nothing wakes an idle Codex. Codex has no periodic or idle event. `SessionEnd` fires only after 30 idle minutes. Thus Codex must call `wait_for_message` and loop while it waits for a peer. This call is event-driven and waits up to 600 s. The open path is the Codex app-server. It exposes `turn/start` / `turn/steer` on the shared local daemon. toxi does not use it yet.

If `TOXI_HOME` is not in the environment of the hooks, they read it from `[mcp_servers.toxi] env` in `~/.codex/config.toml`. In Codex, use `/hooks` once to review and trust the hooks.

The hook commands find the plugin through `PLUGIN_ROOT`, which Codex sets. If a script is missing, the hook command exits 0. The reason is that Codex reads exit 2 from a Stop hook as "do not stop". If an upgrade changes `hooks.json`, Codex shows the hooks as untrusted again. In this case, trust the hooks in `/hooks`. Then restart the session.

**Read on demand**: `toxi setup codex` installs the custom prompt `/prompts:toxi-read [alias]`. This prompt reads unread messages through the MCP tools and marks them as read. It does not run `toxi unread` in the shell, because the shell of Codex does not have the `TOXI_HOME` of the MCP server.

## Send / identity / safety

Codex uses the same MCP tools as Claude Code. Usually, Codex runs its own identity. This identity uses `TOXI_HOME=~/.config/toxi-codex` under `[mcp_servers.toxi] env` in `~/.codex/config.toml`. The hook also reads that env. To set the card of Codex, run `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>`. The skill repeats the untrusted-input boundary and the trust levels.

## Install

Run `toxi setup codex`. From a checkout, it registers the MCP server, the repo marketplace and the plugin. `toxi doctor` reports the state. `toxi teardown-codex` removes the setup.
