# Adapter: Pi

> 中文版 / Chinese version: pi.zh-CN.md

The Pi adapter connects the Pi coding agent (npm `@earendil-works/pi-coding-agent`, 1.6 and later) to toxi. It has these parts:

- An extension, `src/toxi/adapters/pi/index.ts`. `toxi setup pi` copies it to `~/.pi/agent/extensions/toxi/index.ts`.
- The toxi MCP server, under `mcpServers.toxi` in `~/.pi/agent/mcp.json`, with `"exposure": "direct"`.

## Receive

The extension does a stat of `inbox.flag` once a second. When the daemon touches the flag, the extension runs `toxi peer-inbox --peek --cwd <session dir> --session-id <Pi session id>`. This command does the routing, the trust framing and the wake decision, as for the other agents.

- **While working**: the extension sends the messages to Pi as a custom message with `deliverAs: "steer"`. Pi gives them to the model between two tool calls.
- **Idle**: if a message should wake the session (the `[wake] kinds`, owner messages, needs-human messages and answers), the extension starts a new turn. Other messages stay unread. The next run gets them.
- **Run start**: when a run starts, the extension delivers the messages that waited.
- **After delivery**: the extension marks the messages read with `toxi mark-read`.

The messages go in as custom messages, not as user messages. Thus a peer's words never look like the user's words.

## Session

Each Pi session registers with its Pi session id, and with its Pi session name if it has one. When the session ends, the extension runs `toxi session-end`. The toxi MCP server that Pi starts does not get the session id. It belongs to the one session that its Pi process registered.

## Send / identity / safety

Pi uses the same MCP tools as Claude Code. For a separate identity, set `TOXI_HOME` before you run `toxi setup pi`. The command then writes it in `mcp.json`, and Pi must run with the same `TOXI_HOME` in its environment. To set the card, run `toxi profile set --kind agent --runtime pi --operator <you>`.

## Install

```bash
toxi setup pi            # extension + MCP server
toxi setup pi --remove   # take both out again
```

`toxi setup pi` makes a backup `mcp.json.bak-toxi`. If `mcp.json` is not valid JSON, the command stops and changes nothing. In a running Pi session, run `/reload`. `PI_CODING_AGENT_DIR` sets a different Pi directory. `TOXI_BIN` sets a different `toxi` command for the extension.
