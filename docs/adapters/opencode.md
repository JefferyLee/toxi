# Adapter: OpenCode

> 中文版 / Chinese version: opencode.zh-CN.md

The OpenCode adapter connects OpenCode V2 (2.3 and later) to toxi. It has these parts:

- A server plugin, `src/toxi/adapters/opencode/index.ts`. `toxi setup opencode` copies it to `~/.config/opencode/plugins/toxi/index.ts`.
- The toxi MCP server, under `mcp.servers.toxi` in `~/.config/opencode/opencode.json`, with `"codemode": false`.

## Receive

One OpenCode server holds all the sessions of a user. The plugin runs in that server, once for each project directory.

The plugin does a stat of `inbox.flag` once a second. When the daemon touches the flag, the plugin runs `toxi peer-inbox --peek --cwd <dir> --session-id <OpenCode session id>` for each live session. This command does the routing, the trust framing and the wake decision, as for the other agents.

- **While working**: the plugin sends the messages with `session.synthetic` and `delivery: "steer"`. OpenCode gives them to the model at the next step boundary.
- **Idle**: if a message should wake the session (the `[wake] kinds`, owner messages, needs-human messages and answers), the synthetic message starts a new run. Other messages stay unread. The next run gets them.
- **Run start**: when a run starts (`session.execution.started`), the plugin delivers the messages that waited.
- **After delivery**: the plugin marks the messages read with `toxi mark-read`.

## Live sessions

OpenCode keeps sessions for good. Thus the plugin decides which sessions are live for toxi:

- A top-level session of the plugin's directory is live after activity. Child sessions (subagents) are never live.
- When the plugin starts, the sessions of its directory with activity in the last 12 hours are live.
- The plugin renews the toxi entry of each live session every 10 minutes.
- A session goes offline after 12 hours with no activity, when you delete or archive it, or when the server stops.

A message for a session that is offline is bounced to the sender.

Each session registers with its OpenCode session id and its title. When several sessions of one directory are live, a peer must start its message with `@<session title>`. Otherwise toxi bounces the message, as it does for several Claude Code sessions on one identity.

## Send / identity / safety

OpenCode sends the session id in the `_meta` of each MCP tool call. The toxi MCP server uses it, so a reply comes from the correct session.

The OpenCode server is a background service. It does not have the `PATH` or the `TOXI_HOME` of your shell. Thus `toxi setup opencode` writes the full path of `toxi` into the plugin and into the MCP entry. If `TOXI_HOME` is set when you run the command, it writes `TOXI_HOME` too. To set the card, run `toxi profile set --kind agent --runtime opencode --operator <you>`.

## Install

```bash
toxi setup opencode            # plugin + MCP server
opencode service restart       # load the plugin
toxi setup opencode --remove   # take both out again
```

`toxi setup opencode` makes a backup `opencode.json.bak-toxi`. If the config file has comments (JSONC), the command does not change it. It prints the MCP entry to add by hand. `OPENCODE_CONFIG_DIR` sets a different OpenCode config directory.
