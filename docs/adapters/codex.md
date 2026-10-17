# Adapter: Codex

> 中文版 / Chinese version: codex.zh-CN.md
> Day-to-day usage: [../codex-usage.md](../codex-usage.md).

`plugins/toxi/` wires Codex: `.mcp.json` starts `toxi mcp serve`, `hooks/stop_hook.py` is a Stop hook, `skills/toxi/SKILL.md` teaches Codex the rules.

## Receive

Codex CLI (0.161+) runs the same hook events as Claude Code, so the plugin delivers the same way:

- **While working**: a PostToolUse hook (`hooks/peer_check.sh`, a stat of `inbox.flag`, then `toxi peer-inbox`) injects routed peer messages as `additionalContext` after a tool call.
- **Session start / resume**: `hooks/session_start_hook.py` prints the routed peer inbox as developer context.
- **End of turn**: the Stop hook runs `toxi peer-inbox` and blocks the stop with the messages when there are any; otherwise it emits the `toxi statusline` summary as a system message.
- **Idle**: nothing wakes an idle Codex (Codex has no periodic or idle event; `SessionEnd` fires only after 30 idle minutes). It must call `wait_for_message` (event-driven, up to 600 s) and loop while it waits for a peer.

Hooks read `TOXI_HOME` from `[mcp_servers.toxi] env` in `~/.codex/config.toml` when it is not in their environment. Review and trust them once with `/hooks` in Codex.

The hook commands find the plugin through `PLUGIN_ROOT`, which Codex sets, and exit 0 when a script is missing (Codex reads exit 2 from a Stop hook as "do not stop"). After an upgrade that changes `hooks.json`, Codex shows the hooks as untrusted again: trust them in `/hooks` and restart the session.

**Read on demand**: `toxi setup codex` installs the custom prompt `/prompts:toxi-read [alias]`, which reads unread messages through the MCP tools and marks them read. It does not shell out to `toxi unread`, because Codex's shell lacks the MCP server's `TOXI_HOME`.

## Send / identity / safety

Same MCP tools as Claude Code. Codex usually runs its own identity (`TOXI_HOME=~/.config/toxi-codex` under `[mcp_servers.toxi] env` in `~/.codex/config.toml`; the hook reads that env too). Set its card with `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>`. The skill restates the untrusted-input boundary and the trust levels.

## Install

`toxi setup codex` (from a checkout: registers the MCP server, the repo marketplace and the plugin); `toxi doctor` reports the state; `toxi teardown-codex` removes it.
