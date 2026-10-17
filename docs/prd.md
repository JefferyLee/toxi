# toxi — Claude Code CLI Chat Plugin (PRD)

> 🌐 Languages: **English** | [中文](prd.zh-CN.md)

**Version**: v0.1
**Date**: 2026-05-27
**Status**: v0.1 MVP complete; Claude Code integration shipped; packaged as a plugin (see §5.1)

---

## 1. Project overview

### 1.1 One-line definition

A command-line chat plugin embedded in Claude Code that lets developers talk to friends **asynchronously, encrypted, and decentralized** while coding — no central server required.

### 1.2 Background

Developers using Claude Code often need to message colleagues or friends but don't want to be interrupted by real-time IM (Slack, WeChat, Telegram). This plugin offers:

- **Don't-disturb**: messages surface only when the user actively checks
- **Decentralized**: no company servers; friends communicate directly
- **Encrypted**: end-to-end, minimal metadata
- **AI-integrated**: Claude can summarize/search messages in the future

### 1.3 Design philosophy

1. **Simple over perfect**: get two-party chat working first; multi-party / multi-device later
2. **Local-first**: all data lives locally; no cloud
3. **Async-first**: not real-time; "eventual delivery"
4. **CLI-first**: native command-line UX, scriptable and AI-callable

### 1.4 Non-goals (explicitly not doing)

- ❌ Voice / video calls
- ❌ Group chat (not in v1)
- ❌ Multi-device sync (one user, multiple machines)
- ❌ Real-time push / ring notifications
- ❌ Web UI / GUI
- ❌ Mobile

---

## 2. User stories

### 2.1 Core scenarios

**Scenario A: send an async message**
```
Alice is coding and wants to ask Bob something.
$ toxi send bob "can you take a look at the PR I just pushed?"
✓ sent
(Alice keeps coding; doesn't wait for a reply)
```

**Scenario B: check unread messages**
```
$ toxi unread
[3 unread]
1. bob (10 min ago): looked at it; suggest renaming the errorhandler
2. carol (1 hour ago): hiking this weekend?
3. bob (2 min ago): also a typo on line 42
```

**Scenario C: send while the recipient is offline**
```
$ toxi send bob "good night"
✓ recipient is offline; will send when they come online (queued locally)

$ toxi queue
[2 queued]
- bob: "good night" (queued 5 min ago)
- carol: "meeting tomorrow 9am" (queued 1 hour ago)
```

**Scenario D: add a friend**
```
Bob shares his Tox ID with Alice through some other channel (email, WeChat, etc.).

$ toxi add bob 76518406F6A9F2217E8DC487...   (76-char Tox ID)
✓ Added bob to contacts.
  Sending friend request... waiting for them to accept.

# Alice's own Tox ID:
$ toxi me
Your Tox ID: A1B2C3D4E5F6...
(Share this with friends so they can add you)
```

**Scenario E: introduce someone**
```
Alice wants to introduce Carol to Bob:
$ toxi introduce bob carol
✓ Sent Carol's contact to bob

# Bob sees:
$ toxi unread
[1 contact-share invitation]
- alice introduced you to carol (Tox ID: F1E2D3...)
  Accept [y/n]?
```

**Scenario F: work with Claude**
```
$ toxi ask "what did bob say last time about the errorhandler?"
(Claude searches the chat history and answers)

$ toxi send bob --draft-with-claude "write a thank-you note for the review"
(Claude drafts; user confirms before sending)
```

### 2.2 User personas

**Primary users**:
- Developers using Claude Code
- Privacy-minded technical people interested in decentralization
- Core members of small teams (2–10 people)

**Typical scale**: each user's contact list is 5–50 people.

---

## 3. Architecture

### 3.1 System component diagram

```
┌──────────────────────────────────────────────────────────┐
│  User layer                                              │
│                                                          │
│  ┌─────────────────┐    ┌────────────────────────────┐   │
│  │  Claude Code    │    │  CLI commands              │   │
│  │  (chat context) │    │  $ toxi send / read / add  │   │
│  └────────┬────────┘    └─────────────┬──────────────┘   │
│           │                            │                 │
│           └────────────┬───────────────┘                 │
│                        │                                 │
│              IPC (Unix socket / Named pipe)              │
└────────────────────────┼─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│  Daemon process (resident background)                    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  IPC server                                       │    │
│  └──────────────────────┬───────────────────────────┘    │
│                         │                                │
│  ┌──────────────────────┴───────────────────────────┐    │
│  │  Business logic                                   │    │
│  │  - send / receive / queue messages                │    │
│  │  - contact management                             │    │
│  │  - app-layer protocol (contact_share, ack, ...)   │    │
│  └────┬──────────────────────────────────┬──────────┘    │
│       │                                  │               │
│  ┌────▼─────────────────┐    ┌──────────▼──────────┐     │
│  │  Local store (SQLite)│    │  Tox protocol layer │     │
│  │  - messages          │    │  (ctypes/libtoxcore)│     │
│  │  - contacts          │    │  - DHT bootstrap    │     │
│  │  - queue             │    │  - encryption / P2P │     │
│  │  - settings          │    │  - NAT traversal    │     │
│  └──────────────────────┘    └──────────┬──────────┘     │
└─────────────────────────────────────────┼────────────────┘
                                          │
                                  Tox UDP P2P
                                          │
                                          ▼
                              ┌─────────────────────┐
                              │  Tox DHT network    │
                              │  + friends' daemons │
                              └─────────────────────┘
```

### 3.2 Process model

**Two processes**:

1. **`toxi-daemon`**: long-running background process
   - Launched at user login via systemd / launchd / a scheduled task
   - Keeps a Tox instance running and maintains the DHT connection
   - Listens on IPC, handles CLI requests
   - Receives messages and writes them to SQLite

2. **`toxi`**: the CLI invoked for each command
   - Short-lived: runs one command and exits
   - Talks to the daemon over IPC
   - Formats output for the terminal

**Why two processes?**
- The Tox protocol must stay online to keep DHT routing and to receive messages
- A user's CLI commands are on-demand; we can't expect them to keep a shell open forever
- IPC is much faster than starting a fresh Tox instance per command (Tox needs several seconds to reconnect to the DHT)

### 3.3 Tech stack

| Component | Choice | Reason |
|---|---|---|
| **Language** | Python 3.10+ | Claude Code users likely have Python; stdlib `ctypes` can bind C libraries directly |
| **Tox layer** | **ctypes binding to libtoxcore** | py-toxcore-c was verified unusable (see below); using stdlib `ctypes` to call c-toxcore's stable C ABI |
| **System dependency** | libtoxcore (c-toxcore 0.2.x) | macOS `brew install toxcore`; Linux distro package. The only non-Python dependency |
| **Local storage** | SQLite (stdlib) | Zero dependencies, single file, sufficient |
| **IPC** | Unix domain socket (Linux/macOS) / Named pipe (Windows) | Safe, fast, local-only |
| **CLI framework** | Click | Clear sub-commands |
| **Packaging** | pipx | One-command install |

**Why not py-toxcore-c (the original choice)?**
- PyPI only has 0.2.0 (2020, sdist + Cython). It builds a wheel on Python 3.14, but **at runtime `tox_bootstrap` and `tox_self_set_name` segfault immediately** — there is an ABI / struct mismatch with toxcore 0.2.22. Without bootstrap there is no DHT, so the binding is effectively broken.
- A spike verified the alternative: with stdlib `ctypes` binding `libtoxcore` directly, end-to-end works (two instances connect via DHT, befriend each other, exchange a message). Only ~10 functions + 4 callbacks need binding, and the dependency footprint is cleaner (no PyPI Tox package required).

**Why not Node.js?**
- Python's ecosystem (scientific computing, data) is better suited to future "work with Claude" features
- c-toxcore is a stable C library that any language can bind, so the implementation cost is similar and a future rewrite is feasible

### 3.4 Source layout (current)

```
toxi/                            (GitHub: JefferyLee/toxi)
├── pyproject.toml               # hatchling; deps: click; extras: [mcp] [dev]
├── README.md / README.zh-CN.md  # bilingual user docs
├── docs/                        # bilingual design docs (this PRD lives here)
├── src/toxi/             # the engine (PyPI distribution name: `toxi`)
│   ├── paths.py                 # config dir; TOXI_HOME override for test isolation
│   ├── db.py                    # SQLite schema (§4.1, §4.2, §4.5) + idempotent connect()
│   ├── ipc.py                   # length-prefixed JSON frame codec (§4.6.2)
│   ├── client.py                # thin IPC client shared by CLI and tests
│   ├── envelope.py              # app-layer message envelope (§4.2.3)
│   ├── config.py                # config.toml reader (§4.8)
│   ├── tox.py                   # ctypes binding to libtoxcore (savedata identity persistence)
│   ├── daemon.py                # resident process (Tox loop + IPC + ACK retry sweep)
│   ├── cli.py                   # `toxi` CLI (incl. --json group flag and `toxi mcp serve`)
│   └── mcp_server.py            # FastMCP server exposed by `toxi mcp serve` (§4.12)
├── tests/                       # 40 fast + 5 DHT-marked integration tests
├── claude-code-plugin/          # the Claude Code plugin (§4.12)
│   ├── .claude-plugin/plugin.json
│   ├── commands/                # /unread, /send, /contacts, /status
│   ├── hooks/hooks.json         # SessionStart unread-notification hook
│   ├── hooks/unread_hook.py
│   └── .mcp.json                # registers `toxi mcp serve`
├── plugins/toxi/                # experimental Codex plugin (§4.12)
│   ├── .codex-plugin/plugin.json
│   ├── hooks/                   # Stop statusline hook
│   ├── skills/toxi/SKILL.md     # reusable Codex workflow instructions
│   └── .mcp.json                # registers `toxi mcp serve`
├── .agents/plugins/marketplace.json  # Codex repo marketplace entry
├── .claude-plugin/marketplace.json  # this repo IS its own marketplace
└── packaging/homebrew/toxi.rb    # tap formula template (§4.13)
```

Conventions:
- **The daemon is the sole writer of SQLite**; the CLI accesses data only via IPC, never opening the database directly.
- `tox.py` doesn't depend on any PyPI Tox package; at runtime it only needs the system's libtoxcore.
- **Code (everything except `docs/`) is English-only**; **docs are bilingual** (English file is canonical, `<name>.zh-CN.md` is Chinese, with a language switcher at the top of each).
- **Distribution name is `toxi`**; the import package stays `toxi`, and the on-disk config dir stays `~/.config/toxi/` (see §4.13).

---

## 4. Detailed design

### 4.1 Identity and contacts

#### 4.1.1 User identity

Each user's identity = a Curve25519 key pair (generated by toxcore).
- **Public key (Tox ID)**: 76 hex characters, e.g.:
  ```
  76518406F6A9F2217E8DC487BCE0B22A1D8E68F50F3B9C8D... (76 chars total)
  ```
  The Tox ID is actually 32 bytes of public key + 4 bytes of nospam + 2 bytes of checksum.
- **Private key**: never leaves the machine; stored in the encrypted `tox_state.bin` file.

**Identity persistence**:
- The first time the daemon starts, it generates a key pair
- Saved to `~/.config/toxi/tox_state.bin` (no password by default; optional encryption)
- Users can run `toxi me` to see and share their Tox ID

#### 4.1.2 Contact model

```sql
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tox_id TEXT UNIQUE,                     -- 76-char full Tox ID; known only when WE added
                                            -- THEM. A contact added via "accept request" is NULL
                                            -- (we only learn their public key)
    public_key TEXT NOT NULL UNIQUE,       -- 64-char raw public key (always known; stable id)
    alias TEXT NOT NULL UNIQUE,            -- local alias the user gave them, e.g. 'bob'
    display_name TEXT,                     -- their display name (from the Tox protocol)
    status_message TEXT,                   -- their status message
    added_at INTEGER NOT NULL,             -- when added (unix timestamp)
    added_by TEXT,                         -- source: 'manual' / 'introduce:alice'
    last_seen INTEGER,                     -- last time they were online
    is_online BOOLEAN DEFAULT 0,
    friend_number INTEGER,                 -- toxcore's internal friend id (changes across restarts)
    notes TEXT                             -- user's private note
);
```

**Friend requests carry only the public key**: a Tox friend request transmits the 32-byte public key (plus a text message) but **NOT the full Tox ID** (which also has the nospam + checksum). So the accepting side cannot learn the requester's `tox_id`, only the `public_key` — which is why the column above is nullable. Pending friend requests live in their own table:

```sql
CREATE TABLE friend_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL UNIQUE,       -- 64-char requester public key
    message TEXT,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL                    -- 'pending' / 'accepted' / 'rejected'
);
```

**Key design points**:
- **alias is local**: Alice calls her friend `bob`; Bob himself doesn't know that; Bob may call Alice `boss`
- **alias must be unique** (within your own contacts); it is the target argument for CLI commands
- **friend_number is volatile**: toxcore reassigns it across restarts; the daemon remaps from `public_key` at startup

#### 4.1.3 Adding-a-friend flow

```
Alice adds Bob:

1. Alice obtains Bob's Tox ID (any channel: in person, email, etc.)
2. $ toxi add bob <bob_tox_id>
3. The daemon calls tox_friend_add(), sending a friend request (with optional text)
4. The request reaches Bob's daemon via the DHT
5. Bob sees the request: $ toxi requests
   - public key A1B2... (64 chars): "hi I'm alice, friend me?"
6. Bob accepts and assigns a local alias:
   $ toxi accept alice <pubkey-prefix>
   The daemon uses the request's public key to call tox_friend_add_norequest()
7. Each side sees the other in their contact list (on Bob's side, alice's tox_id is NULL)
```

**Important details**:
- The friend-request text is capped at 1016 bytes (a Tox protocol limit)
- When accepting, Bob must give Alice a local alias
- If Bob rejects, Alice gets no notification (a Tox design choice, protecting the rejecter)

### 4.2 Message model

#### 4.2.1 Database schema

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_uuid TEXT NOT NULL UNIQUE,         -- app-layer UUID for dedup and ack
    contact_id INTEGER NOT NULL,           -- FK to contacts
    direction TEXT NOT NULL,               -- 'in' / 'out'
    msg_type TEXT NOT NULL,                -- 'text' / 'contact_share' / 'ack' / 'system'
    content TEXT NOT NULL,                 -- message body (JSON or plain text)
    created_at INTEGER NOT NULL,           -- sender's clock
    received_at INTEGER,                   -- when we received it
    status TEXT NOT NULL,                  -- see the state machine
    delivered_at INTEGER,                  -- when the other side ACKed
    read_at INTEGER,                       -- when the user read it
    last_attempt_at INTEGER,               -- when we last (re)sent; drives ACK-timeout retry
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);

CREATE INDEX idx_messages_contact ON messages(contact_id, created_at DESC);
CREATE INDEX idx_messages_status ON messages(status);
```

#### 4.2.2 Message state machine

**Outbound (direction='out') states**:
```
queued ──┬──> sent ──> delivered ──> read
         │
         └──> failed
```

| State | Meaning | Trigger |
|---|---|---|
| `queued` | Waiting in the local queue; recipient offline | `toxi send` while recipient is offline |
| `sent` | Sent over the Tox protocol | `tox_friend_send_message` returned ok |
| `delivered` | Recipient's daemon stored it | We received the recipient's ack message |
| `read` | Recipient's user actually saw it | We received a read receipt (optional) |
| `failed` | Multiple retries failed | See §4.4 retry policy |

**Inbound (direction='in') states**:
```
received ──> read
```

| State | Meaning |
|---|---|
| `received` | Stored locally, unread |
| `read` | User viewed it via `toxi unread` or `toxi read` |

#### 4.2.3 Application-layer message protocol

The Tox protocol only provides "send bytes to a friend". All structure lives in the application layer.

**Envelope format** (JSON):

```json
{
  "v": 1,                          // protocol version
  "uuid": "550e8400-e29b-41d4-...", // unique message id
  "type": "text",                  // message type
  "ts": 1716700000,                // sender timestamp
  "data": { ... }                   // type-specific payload
}
```

**Message types**:

| type | data | description |
|---|---|---|
| `text` | `{"body": "hello"}` | plain text message |
| `ack` | `{"ref_uuid": "..."}` | delivery confirmation |
| `read_receipt` | `{"ref_uuid": "..."}` | read receipt (optional) |
| `contact_share` | `{"tox_id": "...", "suggested_alias": "carol", "from_alias": "alice"}` | forward contact info |
| `typing` | `{}` | (future) typing indicator |
| `presence` | `{"status": "busy"}` | (future) presence/status |

**Per-message Tox length limit**: 1372 bytes (`MAX_MESSAGE_LENGTH`).

If a body is too long, the app layer would need fragmentation:
```json
{
  "v": 1, "uuid": "...", "type": "text",
  "data": {
    "body": "...",
    "chunk": 1, "total_chunks": 3, "chunk_id": "..."
  }
}
```

But v1 does NOT fragment. **The implementation validates by encoded bytes**: the whole envelope (after JSON encoding) must be ≤ `TOX_MAX_MESSAGE_LENGTH` (1372 bytes); otherwise `send` returns `MESSAGE_TOO_LONG`. We measure by bytes rather than characters because the same character count occupies more bytes for CJK and other multi-byte text.

### 4.3 Offline message queue

#### 4.3.1 Core problem

**The Tox protocol does not support offline messages.** `tox_friend_send_message()` must be called while the recipient is online or the message is lost.

**Solution**: maintain a queue on the **sender** side and resend once the recipient comes online.

#### 4.3.2 Queue design

The queue is just the rows in `messages` where `direction='out' AND status='queued'`.

```sql
CREATE INDEX idx_queue ON messages(contact_id, status, created_at)
WHERE status = 'queued';
```

#### 4.3.3 Send flow

```python
def send_message(contact_alias, body):
    contact = db.get_contact(alias=contact_alias)
    msg_uuid = generate_uuid()
    envelope = {
        "v": 1, "uuid": msg_uuid, "type": "text",
        "ts": int(time.time()), "data": {"body": body}
    }
    payload = json.dumps(envelope)

    # Persist first (durability before delivery)
    db.insert_message(
        msg_uuid=msg_uuid,
        contact_id=contact.id,
        direction='out',
        msg_type='text',
        content=body,
        status='queued',
        created_at=envelope['ts']
    )

    # Try to send immediately
    if contact.is_online:
        try:
            tox.friend_send_message(contact.friend_number, payload)
            db.update_message_status(msg_uuid, 'sent')
            return "sent"
        except ToxError as e:
            # "online" status may be stale; keep as queued
            log.warning(f"send failed: {e}")
            return "recipient just went offline; queued"
    else:
        return "recipient offline; queued"
```

#### 4.3.4 Trigger resend when friend comes online

Using Tox's `friend_connection_status` callback:

```python
def on_friend_connection_status(friend_number, connection_status):
    """toxcore callback: friend came online/offline"""
    contact = db.get_contact(friend_number=friend_number)
    if connection_status != TOX_CONNECTION_NONE:
        # Online: trigger queue flush
        contact.is_online = True
        db.update(contact)
        flush_queue(contact.id)
    else:
        contact.is_online = False
        db.update(contact)

def flush_queue(contact_id):
    """Resend all queued messages for this friend in order"""
    queued = db.get_messages(
        contact_id=contact_id,
        status='queued',
        order_by='created_at ASC'
    )
    contact = db.get_contact(id=contact_id)

    for msg in queued:
        envelope = build_envelope_from_db(msg)
        try:
            tox.friend_send_message(contact.friend_number, json.dumps(envelope))
            db.update_message_status(msg.msg_uuid, 'sent')
        except ToxError:
            # Send failed; stay queued and retry later
            break  # don't continue, to preserve order
```

#### 4.3.5 Receiver side

```python
def on_friend_message(friend_number, message_text):
    """toxcore callback: received a message"""
    try:
        envelope = json.loads(message_text)
    except json.JSONDecodeError:
        # Not our protocol — likely from another Tox client; store as plain text
        envelope = {"v": 1, "type": "text", "data": {"body": message_text}}

    if envelope.get("type") == "text":
        store_incoming_text(friend_number, envelope)
        send_ack(friend_number, envelope["uuid"])
    elif envelope.get("type") == "ack":
        handle_ack(friend_number, envelope["data"]["ref_uuid"])
    elif envelope.get("type") == "contact_share":
        handle_contact_share(friend_number, envelope)
    # ...
```

#### 4.3.6 Delivery confirmation (ACK)

The sender needs to know the message really reached the recipient's local store (not just the Tox protocol layer).

```
Alice → Bob: {type: "text", uuid: "X", data: {body: "hi"}}
Bob's daemon: writes to SQLite, immediately replies with ACK
Bob → Alice: {type: "ack", data: {ref_uuid: "X"}}
Alice's daemon: flips message X from sent to delivered
```

**Why an app-layer ACK?**
- The Tox protocol's "sent" only means the UDP packet went out, not that the other process actually processed it
- The other process could have crashed, run out of disk, or have a bug

### 4.4 Retry and reliability

#### 4.4.1 Retry policy

```
'queued' messages:
  - only attempted when the recipient is online
  - no exponential backoff (we're event-triggered, not polling)

'sent' messages (sent, no ack yet):
  - 5 minutes without an ack → resend once (mark as sent_retry)
  - 30 minutes without an ack → resend once
  - 24 hours without an ack → mark as failed; alert the user

Dedup:
  - The receiver dedups on msg_uuid (each uuid stored once)
  - But still ACKs (so the sender knows)
```

**v1 implementation simplification**: no 5/30-minute staged retry and no separate `sent_retry` state. The daemon sweeps unacked `sent` messages every 30 seconds — if the friend is online and `now - last_attempt_at > ack_timeout_minutes`, it resends (using the original uuid; the receiver dedups but still ACKs, which recovers `delivered`); if `now - created_at > fail_after_hours`, it marks `failed`. Thresholds come from `config.toml [retry]` (`max_retries` is not yet used).

#### 4.4.2 NAT keep-alive

Tox DHT nodes send their own heartbeats. The app could augment them:
- Send a `presence` message to every online friend every 4 hours
- In practice toxcore already does this well; the app doesn't have to

#### 4.4.3 Daemon crash recovery

- All state persists in SQLite
- On daemon restart:
  1. Load the Tox state (keys, friend list)
  2. Connect to DHT bootstrap nodes
  3. Wait for the DHT connection (`self_connection_status` callback)
  4. Wait for friend-online callbacks
  5. Auto-flush the queue

### 4.5 Contact forwarding (introduce)

#### 4.5.1 Flow

```
Alice wants to introduce Carol to Bob:

1. $ toxi introduce bob carol
2. The daemon checks:
   - Is bob in my contacts? ✓
   - Is carol in my contacts? ✓
3. Build the contact_share message:
   {
     "type": "contact_share",
     "data": {
       "tox_id": "<carol's full tox id>",
       "suggested_alias": "carol",      // alice's local name for carol
       "from_alias": "alice",           // how bob should remember who introduced them
       "note": "my coworker"             // optional intro note
     }
   }
4. Send to bob (goes through the usual queue + retry path)

When bob's daemon receives it:
5. Don't auto-add; queue for review
6. Bob: $ toxi introductions
   - alice introduced you to carol (Tox ID: F1E2...)
     note: my coworker
     accept and assign alias [n/y/rename]?
7. Bob: $ toxi accept-intro alice carol  # default alias
   or  $ toxi accept-intro alice carol --alias=co_carol
8. The daemon sends a friend request to carol
9. Carol handles it like any friend request
```

#### 4.5.2 Schema addition

```sql
CREATE TABLE pending_introductions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_contact_id INTEGER NOT NULL,      -- who introduced
    introduced_tox_id TEXT NOT NULL,       -- the introduced contact's Tox ID
    suggested_alias TEXT,
    note TEXT,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL,                  -- 'pending' / 'accepted' / 'rejected'
    FOREIGN KEY (from_contact_id) REFERENCES contacts(id)
);
```

#### 4.5.3 Security considerations

- **No auto-add**: requires explicit user confirmation; prevents malicious introductions
- **Keep the source**: the new contact's `added_by` is set to `introduce:alice`
- **Silent decline**: declining sends no notice to Alice (privacy, symmetric with friend requests)

**v1 implementation notes**:
- **You can only introduce contacts whose full Tox ID you have**: sending a friend request needs the 38-byte address (with nospam), so a contact with `tox_id IS NULL` (added via "accept request") can't be introduced — `introduce` returns `NO_TOX_ID`.
- **The recipient must be online for `introduce`**: the contact_share is sent live, not queued (the queue currently handles only text); if offline, returns `RECIPIENT_OFFLINE`.
- For `accept-intro <from> <whom>`, the `whom` is the `suggested_alias` of the introduction to pick; the new local alias defaults to `whom`, overridable with `--alias`.

### 4.6 IPC protocol

#### 4.6.1 Transport

- **Linux/macOS**: Unix domain socket, at `~/.config/toxi/daemon.sock`
- **Windows**: Named pipe, `\\.\pipe\claude-toxi-daemon`
- **Permissions**: only the current user can read/write (0600)

#### 4.6.2 Wire format

Length-prefixed JSON:

```
[4 bytes: payload length (big-endian uint32)][payload: JSON]
```

**Request**:
```json
{
  "id": "req-001",         // client-generated; matches a response
  "method": "send_message",
  "params": {
    "alias": "bob",
    "body": "hello"
  }
}
```

**Response**:
```json
{
  "id": "req-001",
  "result": {
    "msg_uuid": "550e8400-...",
    "status": "queued",
    "message": "recipient offline; queued"
  }
}
```

**Error**:
```json
{
  "id": "req-001",
  "error": {
    "code": "CONTACT_NOT_FOUND",
    "message": "contact not found: bob"
  }
}
```

#### 4.6.3 RPC methods

| Method | Params | Returns |
|---|---|---|
| `get_me` | — | `{tox_id, name, status}` |
| `set_name` | `{name}` | OK |
| `add_contact` | `{tox_id, alias, request_msg?}` | OK |
| `accept_request` | `{public_key, alias}` | OK |
| `list_contacts` | `{}` | `[{alias, tox_id, online, last_seen}]` |
| `send_message` | `{alias, body}` | `{msg_uuid, status}` |
| `get_messages` | `{alias?, unread_only?, limit?}` | `[messages]` |
| `mark_read` | `{msg_uuids: [...]}` | `{marked}` |
| `list_queue` | `{}` | `[{alias, body, queued_at}]` |
| `introduce` | `{to_alias, contact_alias, note?}` | OK |
| `list_introductions` | `{}` | `[pending_intros]` |
| `accept_introduction` | `{from_alias, introduced_tox_id, alias}` | OK |
| `get_status` | — | `{dht_connected, friends_online, queue_size}` |

#### 4.6.4 Server push (optional v2)

v1 is polling-based (each CLI command queries explicitly).
v2 may add server-sent events, letting CLI tools subscribe to the message stream.

### 4.7 CLI command reference

**Standalone flag** (no subcommand needed):
- `toxi --version` / `toxi -V` — print the engine version and exit. Reads from `src/toxi/__init__.py` `__version__`; per §4.13 the same value is locked across `pyproject.toml`, both plugin manifests, and `tests/test_versions.py` enforces the lock.

**Global flag** (placed before the subcommand):
- `toxi --json <cmd>` — emit machine-readable JSON instead of human-formatted output for any read command (`me`, `status`, `contacts`, `requests`, `unread`, `read`, `queue`, `introductions`, `send`). In `--json` mode `unread` / `read` are a read-only **peek** and do *not* mark messages read — that lets a hook or MCP tool consume the data without burning the unread state.

Full command list:

```bash
# Identity
toxi init                      # First-time init; generate keys
toxi me                        # Show your own Tox ID and name
toxi set-name "Alice"          # Set your display name

# Contacts
toxi add <alias> <tox_id>      # Add a friend
toxi accept <alias> <pubkey>   # Accept a friend request (pubkey = requester public-key prefix)
toxi requests                  # Pending friend requests
toxi contacts                  # List all contacts
toxi contacts --online         # Online only
toxi remove <alias>            # Remove a contact

# Messaging
toxi send <alias> <message>    # Send a message
toxi send <alias> -            # Read body from stdin
toxi unread                    # All unread
toxi unread <alias>            # Unread from one contact
toxi read <alias>              # History (default last 20)
toxi read <alias> --limit 50
toxi queue                     # Outgoing queue

# Introductions
toxi introduce <to> <whom>     # Introduce one contact to another
toxi introductions             # Received introductions
toxi accept-intro <from> <whom> [--alias=...]

# System
toxi status                    # Daemon status, DHT, friend online state
toxi daemon start/stop/restart
toxi daemon logs
toxi mcp serve                 # Run the MCP server over stdio (see §4.12)
toxi setup-engine              # Identity + daemon only
toxi setup-claude              # Claude Code statusLine + install hints only
toxi setup-codex               # Codex MCP + plugin only
toxi doctor-codex              # Read-only Codex wiring check
toxi teardown-codex            # Remove Codex wiring, preserve identity/history

# Claude collaboration
# Done in v0.1: --json flag, SessionStart unread hook, slash commands, MCP server (§4.12)
# Still v2:
toxi ask <question>            # Let Claude search the history
toxi send <alias> --draft-with-claude <prompt>
```

### 4.8 Data layout

```
~/.config/toxi/
├── tox_state.bin              # Tox internal state (keys, friend list)
├── chat.db                    # main SQLite database
├── daemon.sock                # IPC socket (Linux/macOS)
├── daemon.pid                 # PID
├── daemon.log                 # log
├── config.toml                # user configuration
└── bootstrap.json             # DHT bootstrap node list
```

**Example `config.toml`**:
```toml
[daemon]
log_level = "info"

[tox]
udp_enabled = true
ipv6_enabled = true
# Optional: proxy (e.g. Tor)
# proxy_type = "socks5"
# proxy_host = "127.0.0.1"
# proxy_port = 9050

[ui]
default_history_limit = 20
show_timestamps = true
notify_on_receive = false      # don't-disturb default

[retry]
ack_timeout_minutes = 5
max_retries = 3
fail_after_hours = 24
```

### 4.9 Security

#### 4.9.1 Trust model

| Data | Encryption | Who can see it |
|---|---|---|
| Message content | E2EE (Tox protocol) | Only the sender's and recipient's daemons |
| Metadata | Partial | DHT relays can see that "A and B communicate" (IP layer) |
| Local database | None (v1) | Any process that can access the user's files |
| Tox private key | None (v1) | Same as above |

#### 4.9.2 Known v1 limits

- **Local database is unencrypted**: relies on filesystem permissions
- **Private key is unencrypted**: if the machine is compromised, the identity is stolen
- **IP is exposed**: people you chat with see your public IP (a Tox protocol property, unless you tunnel through Tor)

#### 4.9.3 v2 improvements

- Encrypt `tox_state.bin` and `chat.db` with a master password
- Optional Tor proxy
- "Perfect forward secrecy" key rotation (limited support in Tox)

#### 4.9.4 Abuse resistance

- Reject messages from strangers (must be friends first)
- Rate-limit friend requests (max 5 received per minute)
- Reject large messages (>10KB at the app layer)

### 4.10 Error handling

**Daemon startup failures**:

| Situation | Response |
|---|---|
| Socket / port in use | Exit with error; tell the user to check for an existing daemon |
| All DHT nodes unreachable | Retry, warn, but keep running (may connect later) |
| Corrupt Tox state | Back up, alert the user; cannot auto-recover (lost key = lost identity) |
| Corrupt database | Back up; try VACUUM; manual intervention if that fails |

**Send failures**:

| Situation | Response |
|---|---|
| Recipient offline | Queue (normal) |
| Message too long | CLI rejects (tell user to split) |
| Not a friend | Reject (suggest `add` first) |
| Tox error (rare) | Keep `queued`; retry later |

### 4.11 Logging and observability

**Log levels**:
- `error`: needs user attention
- `warning`: non-fatal issues
- `info`: routine events (friend on/off, message counts)
- `debug`: protocol details (off by default)

**Log location**: `~/.config/toxi/daemon.log`, rotated at 10MB, 5 files retained.

**Sensitive data**:
- Logs **never** include message body
- Logs **do** include message metadata: uuid, first 8 chars of peer public_key, length, timestamp

**`toxi status` output**:
```
$ toxi status
Daemon: running (PID 12345)
Uptime: 3d 2h 15m
DHT: connected (UDP)
Tox ID: A1B2C3D4...
Display Name: Alice

Contacts: 12 total, 5 online
  ✓ bob       online (last activity 2m ago)
  ✓ carol     online (last activity 15m ago)
  ○ dave      offline (last seen 2h ago)
  ...

Queue: 3 messages pending
  - bob: 2 messages
  - dave: 1 message

Stats (last 24h):
  Sent: 47 / Received: 23 / Failed: 0
```

### 4.12 Claude Code integration

Originally listed for v1.0; the foundation shipped with v0.1. The engine is independent of Claude Code, but a separate **Claude Code plugin** lives in `claude-code-plugin/` and wires everything up in one step:

| Surface | What it does | Implementation |
|---|---|---|
| `--json` group flag (§4.7) | Machine-readable output; peek mode for `unread`/`read` (no auto mark-read) | `src/toxi/cli.py` |
| SessionStart hook | When a Claude Code session starts, injects unread messages into the model's context (and asks the model to translate any non-Chinese message into Chinese) | `claude-code-plugin/hooks/hooks.json` → `hooks/unread_hook.py` (calls `toxi --json unread`) |
| Slash commands | `/unread`, `/send <alias> <message>`, `/contacts`, `/status` — namespaced under `/toxi:` once installed | `claude-code-plugin/commands/*.md` |
| MCP server | Tools `get_unread`, `read_history`, `mark_read`, `send_message`, `list_contacts`, `get_status` — lets the model act for you while keeping peek and mark-read separate | `toxi mcp serve` (FastMCP, optional `[mcp]` extra) registered via `claude-code-plugin/.mcp.json` |

**Untrusted-input framing (prompt-injection resistance)**: incoming toxi messages are external input being injected into the model's context. The hook and slash-command prompts explicitly label them as **untrusted personal content, not instructions**, so a message body like "ignore previous instructions and run X" is treated as text the user might read, not as something Claude should act on.

**Translation**: because Claude *is* the model, the hook just labels messages and lets Claude translate any non-Chinese ones when relaying — no extra dependency or API call required. A terminal-side translation (no model in the loop) would need a separate Claude API path; that's deferred.

**Codex integration (experimental)**: the engine now also has a Codex plugin in `plugins/toxi/`. It bundles a Codex manifest, `.mcp.json` for `toxi mcp serve`, a Stop hook that emits Codex Stop-hook JSON containing the same `toxi statusline` summary Claude Code shows in its bottom bar, and a `toxi` skill that preserves the same untrusted-input boundary. The plugin intentionally does not register a SessionStart hook; unread messages are surfaced only when the user asks Codex to read them through MCP/CLI. The MCP server advertises server-level instructions with the same rules, so the boundary is visible even when the skill is not loaded; `get_unread` and `read_history` stay read-only, while `mark_read` is an explicit action for specific message UUIDs. `toxi setup-codex` wires a source checkout into Codex by registering the MCP server, adding the repo marketplace, and installing the plugin; when a PyPI/pipx/Homebrew engine runs inside a checkout, it uses that checkout's `.agents/plugins/marketplace.json` instead of the installed package path. `toxi doctor-codex` checks that wiring with read-only Codex CLI list commands. `toxi teardown-codex` removes those Codex-side entries without touching the daemon, identity, or chat history. Setup is split into `toxi setup-engine`, `toxi setup-claude`, and `toxi setup-codex`; `toxi setup` remains the legacy combined engine + Claude Code path.

**Codex manual acceptance**:
- After `toxi setup-codex` and a passing `toxi doctor-codex`, open Codex and verify `/mcp`, `/plugins`, and `/hooks` show the toxi entries; approve hook trust if prompted.

### 4.13 Distribution and naming

| Layer | Name | How to install |
|---|---|---|
| Engine (PyPI) | `toxi` | `pipx install 'toxi[mcp]'` (published since 0.4.0) |
| Engine (Homebrew) | `toxi` | `brew install <owner>/tap/toxi` — the formula `depends_on "toxcore"` so libtoxcore comes along automatically. Template at `packaging/homebrew/toxi.rb` |
| Claude Code plugin | `toxi` | `toxi setup-claude` wires statusLine and prints install hints; `/plugin marketplace add JefferyLee/toxi` then `/plugin install toxi@toxi`; or `claude --plugin-dir ./claude-code-plugin` for dev |
| Codex plugin | `toxi` | From a checkout: `toxi setup-codex`; verify with `toxi doctor-codex`; remove with `toxi teardown-codex`; repo marketplace entry in `.agents/plugins/marketplace.json`; plugin files live in `plugins/toxi/` |

**Versioning decision**: the engine and bundled plugins use one release version, sourced from `pyproject.toml`. `src/toxi/__init__.py`, `claude-code-plugin/.claude-plugin/plugin.json`, and `plugins/toxi/.codex-plugin/plugin.json` must match it; `tests/test_versions.py` enforces this so marketplace installs and engine upgrades describe the same checkout.

**Test isolation**: ordinary `pytest` skips tests that construct real libtoxcore handles or hit the public Tox DHT. Use `pytest --run-toxcore` for local toxcore/daemon tests and `pytest --run-dht` for slow public-DHT integration tests.

**Why `toxi`**: the engine was originally called `chat`, then `cc-chat` to disambiguate from generic `chat` packages. Both names baked Claude Code into the brand, which doesn't match the long-term direction — the engine is agent-agnostic, with future targets including Codex and Grok Builder. `toxi` keeps the Tox lineage explicit (Tox + i) and stops the brand from collapsing onto one host. The PyPI name `toxi` was available; `cc-chat` was not used on PyPI either, but the new name is shorter, more memorable, and not tied to a single AI tool.
- The on-disk config dir: `~/.config/toxi/` (renaming it would break the user's existing Tox identity and message history).

---

## 5. Implementation plan

### 5.1 MVP (v0.1)

**Goal**: two friends can install, add each other, exchange text messages, with offline buffering.

**Scope**:
- ✅ Single platform (**macOS first**; dev machine is macOS; Linux deferred to v0.2)
- ✅ daemon + CLI two-process model
- ✅ Add friends, send / read messages, queue
- ✅ Contact forwarding
- ✅ SQLite persistence
- ❌ Encrypted local storage
- ❌ Claude collaboration
- ❌ Tor integration
- ❌ Windows

**Estimated code**: ~1500 lines of Python

**Estimated time**: 2–3 weeks (part-time)

**Current progress** (vertical thin slices; each step ends at a verifiable milestone):
- ✅ step 0 Tox spike: ctypes binding to libtoxcore; two instances verified end-to-end
- ✅ step 1 scaffolding: package layout / paths / db / ipc / tox binding + tests (9 fast + 1 DHT integration, all green)
- ✅ step 2 daemon skeleton: Tox event loop + bootstrap + IPC server (get_me/get_status)
- ✅ step 3 CLI skeleton: init/me/status → milestone `toxi me`
- ✅ step 4 contacts: add/accept/requests/contacts + friend callbacks; two daemons befriend over real DHT (milestone met)
- ✅ step 5 online messaging: envelope protocol + send/unread/read/queue; live message round-trip stored (milestone met)
- ✅ step 6 offline queue: reconnect-triggered flush, ordered resend; 10 offline messages all received in order (milestone met, = §8 metric ②)
- ✅ step 7 ACK / delivery state machine: receiver acks → sender sent→delivered; timeout retry, expiry → failed (milestone met)
- ✅ step 8 introduce: contact_share + pending_introductions + accept-intro; Alice introduces Carol to Bob; Bob successfully connects to Carol (milestone met, = §8 metric ③)
- ✅ step 9 polish: README install docs, enriched `toxi status` (§4.11 format), log rotation (10MB×5, §4.11), CLI error-message polish (human-readable only)
- ✅ step 10 Claude Code integration + packaging + rename (§4.12, §4.13): `--json` flag; SessionStart unread hook with translation + prompt-injection framing; slash commands; MCP server (`toxi mcp serve`); all bundled as a Claude Code plugin in `claude-code-plugin/`; repo doubles as a marketplace (`.claude-plugin/marketplace.json`); brew tap formula template; renamed distribution `toxi` → `toxi`; bilingual docs (English `.md` + `.zh-CN.md`)

### 5.2 v0.2

- macOS support
- Better error messages and docs
- daemon systemd / launchd integration
- Basic unit and integration tests

### 5.3 v0.3

- Windows support (Named-pipe IPC)
- Local-database encryption (master password)
- Tor proxy option
- Message search (`toxi search <keyword>`)

### 5.4 v1.0

- ✅ Claude Code native hook for proactive new-message notifications — delivered in v0.1 (§4.12)
- ⬜ `toxi ask` (Claude searches local message history)
- ⬜ `toxi send --draft-with-claude` (Claude drafts a reply, user confirms)
- ⬜ Comprehensive docs and examples

### 5.5 Long-term (v2+)

- Multi-device sync (one identity, multiple machines)
- Group chat (Tox conferences or a custom protocol)
- File transfer
- Mobile / Web UI

---

## 6. Open decisions

Open questions in the design, to be resolved before implementation:

1. **CLI interaction model**
   - A: pure command form (`toxi send bob "..."` one at a time)
   - B: add a REPL mode (`toxi shell` enters a session)
   - Tentatively A; B later

2. **How to present multiple unread messages**
   - Sort by time or group by contact?
   - Tentatively by time, with a `--by-contact` option

3. **Input safety for `toxi send`**
   - Should we avoid shell-history leakage of sensitive content?
   - Tentatively add `--from-file` and `--stdin`; use those for sensitive messages

4. **DHT bootstrap nodes**
   - Use the Tox community list
   - Allow user customization?
   - Tentatively allow override via config.toml

5. **First-launch onboarding**
   - Require an explicit `toxi init` step?
   - Or auto-generate when the daemon first starts?
   - Tentatively explicit `toxi init`, to avoid accidental startup

6. **What if a friend's Tox ID changes** (regenerated key)
   - How does the app recognize "still the same person"?
   - v1: don't; user does `remove + add` manually
   - v2: optional "chain of trust" identity proof

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The Tox ecosystem decays | Long-term maintenance trouble | The protocol is simple and stable; worst case, fork c-toxcore |
| ~~py-toxcore-c is unmaintained~~ (occurred) | binding bugs | **Mitigated**: a spike confirmed py-toxcore-c 0.2.0 segfaults; switched to a ctypes binding against the stable libtoxcore C ABI; the protocol layer is binding-agnostic and replaceable |
| User's NAT is too strict; DHT unreachable | Completely unusable | Tox has a built-in TCP relay (similar to TURN) |
| Friends don't know each other's online state | Poor UX | Daemon shows last_seen to help |
| Message loss (queue file corruption) | Trust erosion | fsync before write; each message its own transaction |
| Performance: SQLite grows over time | Slow startup | Auto-archive messages older than 6 months; optional delete |

---

## 8. Success metrics

v0.1 MVP success criteria:

- ✅ Two developers can install and add each other from home networks
- ✅ Alice sends 10 messages to offline Bob; Bob comes online and receives them all, in order
- ✅ Alice introduces Carol to Bob; Bob successfully adds Carol
- ✅ Daemon runs 7 days / 24 hours without crashing
- ✅ From install to first message < 5 minutes

---

## Appendix A: References

- Tox protocol spec: https://toktok.ltd/spec.html
- c-toxcore source: https://github.com/TokTok/c-toxcore
- toxcore C header (the actual binding reference): `<libtoxcore prefix>/include/tox/tox.h`
- ~~py-toxcore-c~~ (deprecated, segfaults): https://github.com/TokTok/py-toxcore-c
- Tox bootstrap node list: https://nodes.tox.chat
- WebRTC NAT-traversal discussion: (in this conversation history)

## Appendix B: Glossary

| Term | Meaning |
|---|---|
| Tox ID | 76-char friend identity: public key + nospam + checksum |
| DHT | Distributed Hash Table, used for node discovery |
| Daemon | Resident background process |
| IPC | Inter-Process Communication |
| ACK | Acknowledgement, delivery confirmation |
| MVP | Minimum Viable Product |
| E2EE | End-to-End Encryption |
| NAT | Network Address Translation |

---

**Document revision history**

| Version | Date | Changes |
|---|---|---|
| v0.1 draft | 2026-05-26 | First draft |
| v0.1 | 2026-05-26 | Updates from step 0/1: Tox layer changed from py-toxcore-c → ctypes binding to libtoxcore (§3.3, §7); added implemented source layout (§3.4); platform changed to macOS-first with progress added (§5.1) |
| v0.1 | 2026-05-26 | Updates from step 2/3/4: daemon/CLI scaffolding done (§5.1 progress); contact-model correction — `tox_id` nullable, new `friend_requests` table (§4.1.2); `toxi accept` uses public key (§4.1.3, §4.6.3, §4.7), because friend requests carry only the public key |
| v0.1 | 2026-05-26 | Updates from step 5: online messaging done (envelope + send/unread/read/queue); message length validated by encoded bytes ≤1372 (§4.2.3); §5.1 progress |
| v0.1 | 2026-05-26 | Updates from step 6: offline queue + reconnect flush done (§5.1 progress); 10 offline messages received in order, achieving §8 metric ② |
| v0.1 | 2026-05-26 | Updates from step 7: ACK delivery state machine (§4.3.6 acks, sent→delivered); new `messages.last_attempt_at` (§4.2.1); retry simplified to "online resend on timeout / give up after expiry", reads config.toml (§4.4.1) |
| v0.1 | 2026-05-26 | Updates from step 8: introduce done (§5.1 progress, achieving §8 metric ③); add v1 implementation constraints — can only introduce contacts with a full Tox ID; introduce requires the recipient to be online (§4.5.3) |
| v0.1 | 2026-05-26 | Updates from step 9 (partial): added README install docs; `toxi status` enriched to §4.11 format (§5.1 progress) |
| v0.1 | 2026-05-26 | step 9 polish complete: log rotation 10MB×5 (§4.11), `[daemon] log_level` from config.toml, CLI errors show only human-readable message (§5.1 progress). All v0.1 MVP steps complete |
| v0.1 | 2026-05-27 | Align with current state: title and distribution name → `toxi`; updated source layout (§3.4) to current reality; added `--json` and `toxi mcp serve` to CLI reference (§4.7); added §4.12 Claude Code integration and §4.13 Distribution; added step 10 to §5.1 progress; §5.4 v1.0 marked the native hook delivered; moved PRD into `docs/` |
