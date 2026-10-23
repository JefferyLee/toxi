# Architecture

> 中文版 / Chinese version: architecture.zh-CN.md
> Design background and decisions: [agent-comm-design.md](agent-comm-design.md). Wire format: [protocol.md](protocol.md).

toxi is a communication layer between agents and people. It needs no server. Each participant runs a small daemon that holds a Tox identity. Adapters connect that daemon to the runtime of the agent: Claude Code, Codex, a bot on a VM, or a phone.

## Four layers

```
┌─ L4 adapters ──────────────────────────────────────────────────────────────┐
│ Claude Code plugin │ Codex plugin │ headless: toxi agent run / webhook │ people: CLI, ToxiOS │
│  hooks + MCP       │ hooks + MCP  │                                     │                     │
└──────────┬─────────┴──────┬───────┴─────────────────┬───────────────────┴─────────┬───────────┘
           │   IPC: request/response + event subscription (Unix socket, length-prefixed JSON)
┌─ L2 engine: toxi-daemon ───────────────────────────────────────────────────┐
│ identity · contacts · profile cards · peer trust · reassembly · receipts · │
│ queue/retry · rooms · files · archive/search · event bus · SQLite ·        │
│ inbox.flag                                                                 │
└──────────────────────────────────┬─────────────────────────────────────────┘
┌─ L3 protocol: envelope v2 (carried on L1, implemented by L2) ───────────────┐
│ text(+session, part, thread, reply_to, kind, …) · profile · receipt · ack · room · file │
└──────────────────────────────────┬─────────────────────────────────────────┘
┌─ L1 transport: Tox ─────────────────────────────────────────────────────────┐
│ DHT · end-to-end encryption · NAT traversal · friends only · file transfer │
└────────────────────────────────────────────────────────────────────────────┘
```

- **L1 Tox** gives identity, encryption, discovery and NAT traversal. In Tox, an identity is a key pair. toxi binds `libtoxcore` directly through `ctypes`. The binding is in `src/toxi/tox.py`.
- **L2 the daemon** is one single-threaded process for each identity. Its code is in `src/toxi/daemon.py`. The daemon drives `tox_iterate`, serves IPC and owns the SQLite database. It never blocks on an operation. Clients never open the database directly.
- **L3 the protocol** is JSON inside Tox messages. The code is in `src/toxi/envelope.py`, and the specification is in `protocol.md`. Version 2 is backward compatible with version 1 nodes.
- **L4 adapters** translate between the daemon and an agent runtime. They are thin by design. Each adapter does the same four tasks. The adapter contract below gives these tasks.

## Processes and files

| process | what it is | started by |
|---|---|---|
| `toxi-daemon` | the resident engine for one identity. Its directory is `TOXI_HOME`, with the default `~/.config/toxi`. | `toxi daemon start`, or the OS service from `toxi service install` |
| `toxi` | the short-lived CLI. Each command is one IPC round trip. | you, hooks, scripts |
| `toxi mcp serve` | the MCP server that Claude Code or Codex starts. It uses stdio. | the agent runtime |
| hooks | `claude-code-plugin/hooks/*` and `plugins/toxi/hooks/*` | the agent runtime, at lifecycle points |

Each identity directory contains these files:

- `tox_state.bin`: the keys and the friend list.
- `chat.db`: contacts, messages, friend requests, invites, rooms, room members and pending introductions.
- `archive.db`: the messages that the archive operation moved out. The first archive operation creates this file.
- `profile.json`: the profile card of this identity.
- `config.toml`.
- `media/`: received files and outgoing attachments.
- `inbox.flag`, `daemon.sock` and `daemon.log`.
- `daemon.lock`. The daemon holds an exclusive lock on this file while it runs. A second daemon for the same identity cannot start, even if the first daemon is too busy to answer.

All these files are private. The directory mode is 0700 and the file mode is 0600.

## Data flow of one message

1. A `send` request comes from the CLI, from MCP or from a room fan-out. It becomes an IPC `send_message` call. The daemon stores the row as `queued`. If toxcore reports that the friend is connected, the daemon sends the envelope and the row becomes `sent`. The daemon sends long bodies as `part` chunks. It sends bodies over 64 KB as a file.
2. The daemon of the receiver stores the message once. It uses the uuid to remove duplicates. Then it sends an ACK, reassembles the parts and touches `inbox.flag`. It emits `message.new` to subscribers. It also schedules the `on_message` hook and the webhook. This schedule is debounced, and it starts only when the message is complete.
3. The `peer-inbox` command delivers messages into an agent. The Claude Code PostToolUse, Stop and SessionStart hooks call it. `wait_for_message` also calls it. `peer-inbox` marks the messages read. It renders them with the profile card of the sender, the session, the thread tags and the trust-level guide.

   When a message becomes read, the daemon records a receipt as owed (`receipt_pending`). The daemon sends the receipt only if the profile card of the sender lists the `receipt` cap. If the sender is online, the receipt goes at once. If the sender is offline, the receipt goes on reconnect or with the periodic sweep.
4. The sender sees three states in sequence: `delivered`, then `read`, then `handled`. `delivered` comes from the ack, and `read` comes from the receipt. `handled` comes from a `done` receipt. The receiver sends a `done` receipt when it answers with `reply_to`. It also sends one when it marks the message handled with `toxi done` or MCP `mark_handled`. That mark also records the message as handled on the side of the receiver.

   The sender can see these states live on `toxi watch`. The sender can also block with `--wait-read`.

## The adapter contract

An adapter integrates a new runtime with exactly these four tasks:

1. **Receive**: The adapter selects one path for each situation:
   - *Mid-task*: a hook runs `toxi peer-inbox` when `inbox.flag` changed.
   - *End of turn*: a Stop hook.
   - *Idle*: `wait_for_message` on the event stream, `toxi agent run --exec`, or the `on_message_url` webhook.
2. **Send**: The adapter uses `send_message`, `send_file` or `send_room` with the session label. When they apply, the adapter also sets thread, reply_to and kind.
3. **Identity**: The adapter sets the profile card of this identity one time with `toxi profile set`. Then peers see what kind of participant it is.
4. **Safety framing**: The adapter injects messages only through `peer-inbox` or `wait_for_message`. These tools carry the trust-level wording. If `include_body` is not set, the adapter never writes bodies to logs or webhooks.

The `adapters/` directory shows how the Claude Code, Codex and headless adapters do these tasks. `adapters/ios.md` describes ToxiOS.

## Identity and trust in one picture

```
Tox public key  ──►  profile card (self-described, bound to the key by the E2E channel)
       │                 kind · name · runtime · host · operator · role · caps
       ▼
local alias   ──►  [peers.<alias>] trust = observer | collaborator | owner, dirs = [...]
       │
       ▼
every injected line:  alias [agent/grok @ host · operator jeff · session x] (#thread · kind): body
```

The trust level comes from the operator who paired the two identities. The profile card tells you *what* you talk to. The `owner` level marks a device of the user. Messages from an `owner` count as messages from the user.

The daemon delivers a message to a session with these rules, in this order:

- If a message starts with `@name`, it goes only to the session with the label `name`.
- If a message is a reply, it goes back to the session that sent the original message. This route stays valid for 30 minutes.
- In other cases, `dirs` routes a peer to the one session that works with it.

## Where things live

| concern | module |
|---|---|
| ctypes binding | `src/toxi/tox.py` |
| envelope encode/validate | `src/toxi/envelope.py` |
| daemon (IPC, callbacks, queue, parts, receipts, rooms, files, events, hooks) | `src/toxi/daemon.py` |
| archive and search: IPC `archive_messages`, `archive_status` and `search_messages`. A daily auto-archive moves messages into `archive.db`, controlled by `[archive] after_days`. | `src/toxi/daemon.py` |
| config parsing | `src/toxi/config.py` |
| peer delivery and wording | `src/toxi/peers.py` |
| pairing invites | `src/toxi/invite.py` |
| launchd / systemd services | `src/toxi/service.py` |
| CLI | `src/toxi/cli.py` |
| MCP server (tools, annotations, channel mode) | `src/toxi/mcp_server.py` |
| Claude Code plugin | `claude-code-plugin/` |
| Codex plugin | `plugins/toxi/` |
