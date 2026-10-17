# Architecture

> 中文版 / Chinese version: architecture.zh-CN.md
> Design background and decisions: [agent-comm-design.md](agent-comm-design.md). Wire format: [protocol.md](protocol.md).

toxi is a communication layer between agents and people that needs no server: every participant runs a small daemon that holds a Tox identity, and adapters plug that daemon into whatever runs the agent (Claude Code, Codex, a bot on a VM, a phone).

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

- **L1 Tox** gives identity (a key pair), encryption, discovery and NAT traversal. toxi binds `libtoxcore` directly through `ctypes` (`src/toxi/tox.py`).
- **L2 the daemon** (`src/toxi/daemon.py`) is one single-threaded process per identity: it drives `tox_iterate`, serves IPC, owns the SQLite database and never blocks on anything. Clients never open the database themselves.
- **L3 the protocol** (`src/toxi/envelope.py`, spec in `protocol.md`) is JSON inside Tox messages. Version 2 is backward compatible with version 1 nodes.
- **L4 adapters** translate between the daemon and an agent runtime. They are thin by design: every adapter does the same four things (below).

## Processes and files

| process | what it is | started by |
|---|---|---|
| `toxi-daemon` | the resident engine for one identity (`TOXI_HOME`, default `~/.config/toxi`) | `toxi daemon start`, or the OS service from `toxi service install` |
| `toxi` | the short-lived CLI; every command is one IPC round trip | you, hooks, scripts |
| `toxi mcp serve` | the MCP server (stdio) that Claude Code / Codex launch | the agent runtime |
| hooks | `claude-code-plugin/hooks/*` and `plugins/toxi/hooks/*` | the agent runtime at lifecycle points |

Per identity directory: `tox_state.bin` (keys + friend list), `chat.db` (contacts, messages, friend requests, invites, rooms, room members, pending introductions), `archive.db` (messages moved out by archiving; created by the first archive), `profile.json` (own card), `config.toml`, `media/` (received files, outgoing attachments), `inbox.flag`, `daemon.sock`, `daemon.log`. All private (0700 / 0600).

## Data flow of one message

1. `send` (CLI / MCP / room fan-out) → IPC `send_message` → row stored as `queued` → if toxcore says the friend is connected, the envelope goes out and the row becomes `sent` (long bodies as `part` chunks, bodies over 64 KB as a file).
2. The receiver's daemon stores it once (dedup by uuid), ACKs it, reassembles parts, touches `inbox.flag`, emits `message.new` to subscribers, and schedules the `on_message` hook / webhook (debounced, only once the message is complete).
3. Delivery into an agent: Claude Code's PostToolUse / Stop / SessionStart hooks or `wait_for_message` call `peer-inbox`, which marks the messages read and renders them with the sender's card, session, thread tags and the trust-level guide. Marking read records a receipt as owed (`receipt_pending`); it is sent only if the sender's card lists the `receipt` cap — at once if the sender is online, otherwise on reconnect or by the periodic sweep.
4. The sender sees `delivered` (ack), then `read` (receipt), then `handled` (a `done` receipt: the receiver answered with `reply_to`, or marked it handled with `toxi done` / MCP `mark_handled`, which also records it as handled on the receiver's side), live on `toxi watch`, or by blocking with `--wait-read`.

## The adapter contract

Any new runtime is integrated by doing exactly these four things:

1. **Receive** — pick one path per situation: *mid-task* (a hook that runs `toxi peer-inbox` when `inbox.flag` moved), *end of turn* (a Stop hook), *idle* (`wait_for_message` on the event stream, or `toxi agent run --exec`, or the `on_message_url` webhook).
2. **Send** — `send_message` / `send_file` / `send_room` with the session label and, when it applies, thread / reply_to / kind.
3. **Identity** — set this identity's card once (`toxi profile set`), so peers see what kind of participant it is.
4. **Safety framing** — inject messages only through `peer-inbox` / `wait_for_message`, which carry the trust-level wording; never write bodies to logs or webhooks unless `include_body` was chosen.

See `adapters/` for how the Claude Code, Codex and headless adapters implement these, and `adapters/ios.md` for ToxiOS.

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

Trust comes from the operator who paired the two identities; the card tells you *what* you are talking to; `owner` marks the user's own device, whose messages count as the user's. Delivery to sessions: a message starting with `@name` goes only to the session labelled `name`; a fresh reply goes back to the session that sent the message it answers (30 minutes); otherwise `dirs` routes a peer to the one session that works with it.

## Where things live

| concern | module |
|---|---|
| ctypes binding | `src/toxi/tox.py` |
| envelope encode/validate | `src/toxi/envelope.py` |
| daemon (IPC, callbacks, queue, parts, receipts, rooms, files, events, hooks) | `src/toxi/daemon.py` |
| archive and search (IPC `archive_messages`, `archive_status`, `search_messages`; daily auto-archive into `archive.db` by `[archive] after_days`) | `src/toxi/daemon.py` |
| config parsing | `src/toxi/config.py` |
| peer delivery and wording | `src/toxi/peers.py` |
| pairing invites | `src/toxi/invite.py` |
| launchd / systemd services | `src/toxi/service.py` |
| CLI | `src/toxi/cli.py` |
| MCP server (tools, annotations, channel mode) | `src/toxi/mcp_server.py` |
| Claude Code plugin | `claude-code-plugin/` |
| Codex plugin | `plugins/toxi/` |
