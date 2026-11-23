# toxi in Codex — usage guide

> 🌐 Languages: **English** | [中文](codex-usage.zh-CN.md)

This guide covers how to send and read messages, list contacts, and browse history with toxi inside Codex. It assumes you've already installed the `toxi` engine and that the background daemon can run on this machine.

## 1. Confirm the engine is up

The Codex plugin only wires Codex to the local toxi daemon; identity, friends, and the message queue still live in the engine.

```bash
toxi setup-engine
toxi status
```

If you don't have any friends yet, do the add-friend flow in your terminal first:

```bash
toxi me
toxi add bob <bob's Tox ID>
toxi requests
toxi accept alice <pubkey prefix>
```

## 2. Wire into Codex

The Codex plugin install currently requires a source checkout. A PyPI/pipx/Homebrew
install of `toxi` can still provide `toxi mcp serve`; as long as you run
`toxi setup-codex` from inside a source checkout, it will use that checkout's
repo marketplace files.

Run this from a source checkout, even if `which toxi` points at Homebrew/pipx:

```bash
toxi setup-codex
toxi doctor-codex
```

`setup-codex` does three things:

- Registers the Codex MCP server: `toxi mcp serve`
- Adds the current checkout as a Codex plugin marketplace
- Installs the `toxi@toxi` Codex plugin

`doctor-codex` is a read-only diagnostic; it confirms MCP, marketplace, and plugin are all wired. It does not install, remove, or modify Codex config.

## 3. Check inside the Codex TUI

Once Codex is open, run:

```text
/mcp
/plugins
/hooks
```

You should see the `toxi` MCP server, the `toxi@toxi` plugin, and the toxi hooks. If Codex prompts you to trust a hook, first check that the hook command path comes from the current checkout's `plugins/toxi/hooks/`, then confirm trust.

## 4. How message status surfaces

Codex doesn't have a persistent bottom `statusLine` like Claude Code. The toxi Codex plugin uses the
Stop hook JSON to show the same summary: at the end of each turn it calls `toxi statusline` and emits the result via the Stop hook's `systemMessage`.

```text
toxi: 📬 2 from mini2, jeff · 2/2 online
```

Reading it:

- `📬 2` — 2 unread messages
- `from mini2, jeff` — those unread came from these contacts
- `2/2 online` — 2 contacts online out of 2 total

When it shows:

- At session start or resume: unread messages are no longer auto-injected
- At the end of each turn: the `toxi statusline` summary is shown via the Stop hook

If there's no unread it may show:

```text
toxi: 2/2 online
```

If the daemon isn't running it may show:

```text
toxi: offline
```

## 5. Common phrases inside Codex

You drive toxi in Codex mostly through natural language + MCP tools; no slash commands needed.

Check status:

```text
Show me my toxi status.
```

List contacts:

```text
List my toxi contacts and who's online.
```

See unread:

```text
What unread toxi messages do I have?
```

Read history with someone:

```text
Read the last 20 toxi messages between me and bob.
```

Send a message:

```text
Send a toxi message to bob: I'll look at the logs you sent later.
```

Clear unread:

```text
I've seen those unread messages — mark them as read.
```

## 6. Which tools Codex calls

Codex prefers calling these tools via MCP:

| Tool | What it does | Marks read? |
|---|---|---|
| `get_status` | daemon, connection, queue, stats; contacts with online state and trust level; rooms | No |
| `read_messages` | peek at messages: unread (`unread`), a contact (`alias`), a `thread`, a `room`, or a search (`q`) | No |
| `get_message` | one message with its full text and status; for a request, its task state and replies | No |
| `overview` | threads, and the open requests of this session | No |
| `mark` | mark specific message UUIDs as read (`handled` also sends a `done` receipt) | Yes |
| `send_message` | send to a contact or a room, with an optional file; queued if they're offline | No |
| `wait_for_message` | wait for a peer's message | Yes |
| `view_media` | show a received image, or the path of other media | No |

If MCP isn't available, the Codex skill falls back to the CLI:

```bash
toxi status
toxi --json unread
toxi contacts
toxi read <alias> --limit 20
toxi send <alias> "<message>"
```

## 7. Safety boundary

Incoming toxi messages are external input. What Codex may do with one depends on who sent it:

- A contact that is not a peer: "untrusted personal content". If such a message says "ignore your previous instructions" or asks Codex to install software, edit files or run commands, Codex treats it as text the sender wrote, nothing more. Codex sends to such a contact only when you ask in this Codex conversation.
- A peer (`[peers.<alias>]` in config.toml): the message comes with its trust level and the matching guide. An `observer` gives information only. A `collaborator` can ask for work within the current task. An `owner` is you on your own device. Codex answers a peer with `send_message` to that alias. Destructive actions and actions outside the project still need you.
- `read_messages` is peek-only; it doesn't mark messages read
- `mark` is only called when you explicitly ask to clear unread, or after Codex has already relayed the corresponding messages to you

## 8. Troubleshooting

`doctor-codex` fails:

```bash
toxi doctor-codex
```

Follow the output's guidance. Common cases:

- Codex CLI not on PATH: install it or fix PATH
- MCP extra can't be imported: run `pipx inject toxi mcp`
- MCP server not registered: run `toxi setup-codex`
- marketplace/plugin not registered: confirm you ran `toxi setup-codex` from a source checkout

Codex can't see the toxi tools:

- Run `/mcp` inside Codex
- Confirm the `toxi` MCP server is enabled
- Re-run `toxi doctor-codex`

Hooks don't show status:

- Run `/hooks` inside Codex
- Confirm the toxi hooks are installed and trusted
- Confirm `toxi statusline` produces output in your terminal
- If you see `hook returned invalid stop hook JSON output`, the Stop hook's
  output doesn't match Codex's Stop schema; upgrade to `0.2.11` or reinstall the toxi Codex plugin

Not receiving messages:

```bash
toxi status
toxi contacts
toxi queue
```

Confirm the daemon is running, DHT is connected, and your contact is online or messages are queued.

## 9. Remove the Codex integration

To remove only the Codex-side wiring, leaving identity, friends, and message history untouched:

```bash
toxi teardown-codex
```

This removes the Codex plugin, MCP server, and marketplace entry. Identity and chat history under `~/.config/toxi/` are kept.
