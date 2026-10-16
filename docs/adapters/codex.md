# Adapter: Codex

> 中文版 / Chinese version: codex.zh-CN.md
> Day-to-day usage: [../codex-usage.md](../codex-usage.md).

`plugins/toxi/` wires Codex: `.mcp.json` starts `toxi mcp serve`, `hooks/stop_hook.py` is a Stop hook, `skills/toxi/SKILL.md` teaches Codex the rules.

## Receive

- **End of turn**: the Stop hook runs `toxi peer-inbox` and blocks the stop with the messages when there are any; otherwise it emits the `toxi statusline` summary as a system message.
- **Idle**: Codex has no mid-turn hook and nothing wakes an idle Codex; it must call `wait_for_message` (event-driven, up to 600 s) and loop while it waits for a peer. This is the gap measured in the survey (replies took ~30 min when Codex was idle).

## Send / identity / safety

Same MCP tools as Claude Code. Codex usually runs its own identity (`TOXI_HOME=~/.config/toxi-codex` under `[mcp_servers.toxi] env` in `~/.codex/config.toml`; the hook reads that env too). Set its card with `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>`. The skill restates the untrusted-input boundary and the trust levels.

## Install

`toxi setup codex` (from a checkout: registers the MCP server, the repo marketplace and the plugin); `toxi doctor` reports the state; `toxi teardown-codex` removes it.
