# toxi wire protocol v2 — friend-message envelope

> 中文版 / Chinese version: protocol.zh-CN.md
> This specification is normative for every toxi-family implementation. These implementations are the toxi Python daemon, the ToxiOS Go daemon and the ToxiOS iOS app. The reference implementation is `src/toxi/envelope.py` and `src/toxi/daemon.py`. The tests `tests/test_events.py`, `tests/test_threads.py`, `tests/test_rooms.py` and `tests/test_files.py` are the executable specification. For the design background, see `docs/agent-comm-design.md` §5 and §11.

## 1. Transport

- One Tox friend message (`TOX_MESSAGE_TYPE_NORMAL`) carries one JSON envelope. One Tox friend message is at most **1372 bytes**.
- Tox file transfers (`TOX_FILE_KIND_DATA`) carry files.
- The JSON is UTF-8. Non-ASCII characters stay raw. They are not `\uXXXX`-escaped.
- Only friends can send. There is no server.
- The Tox public key of a node identifies the node. The key is 64 hex characters in uppercase. All identity data about a node is linked to that key.

## 2. Envelope

```json
{"v": 2, "uuid": "550e8400-…", "type": "text", "ts": 1760600000, "data": { … }}
```

| field | type | rule |
|---|---|---|
| `v` | int | The protocol version. A v1 peer sends `1`. A v1 peer knows only `text`, `ack` and `contact_share`. |
| `uuid` | string, **≤64 chars** | An id that the sender chooses. A UUID4 (36 chars) is recommended. The receiver dedupes on this id (`INSERT OR IGNORE`). If an id is longer than 64 characters, the receiver truncates it to 64 characters *in every place where it matches ids*. These places are the uuid, `reply_to`, `ref_uuid`, the part id and the file msg_uuid. If the uuid is missing, the receiver synthesizes one. |
| `type` | string | See §3. **The receiver drops an unknown type silently. At most, it logs the type.** |
| `ts` | int | The Unix time of the sender, in seconds. |
| `data` | object | The content depends on the type. **The receiver ignores unknown fields.** An unknown field is never an error. |

Some input is not a JSON object with a `type`. An example is a plain message from another Tox client. The receiver treats this input as `text`, with the raw bytes as `body`.

## 3. Types

### 3.1 `text`

| data field | type / limit | meaning |
|---|---|---|
| `body` | string | The message. This field is required. |
| `from_session` | ≤64 | The label of the session or project of the sender. An example is the name of a Claude Code project directory. The receiver shows it as `session@alias`. |
| `part` | `{id ≤64, i, n}` | This envelope is part `i` of `n` of one logical message `id` (§4). |
| `thread` | ≤64 | The thread id or topic id (`pr-412`). |
| `reply_to` | ≤64 | The `uuid` that this message answers. It can also be a part id or a file msg_uuid. |
| `kind` | `request` `report` `ack` `relay` | If this field is absent, the message is a plain `msg`. A `request` expects an answer. A `report` is a status. An `ack` is a bare "got it". An `ack` **must not notify or wake anyone**. A `relay` repeats the words of a third party (hearsay). |
| `needs_human` | bool | A person must make this decision, not an agent. |
| `tags` | ≤8 strings ≤24 | Free-form labels (`WARN`, `OUTAGE`). |
| `room` | ≤64 | The id of the room for this message (§3.6). |
| `room_msg` | ≤64 | The id that all fan-out copies of one room message share. |
| `relayed` | `{name ≤64, ts}` | The room creator relays room history with this field. The field tells who wrote the original message and when (§3.6). The receiver stores the message as read. The receiver does not notify. |

The receiver must do these tasks:

- Store each `uuid` only once.
- **Send an `ack` for every text that it receives, duplicates included.** The `ack` tells the sender to stop its retries.
- Apply the rate limit (§6).
- Show `from_session` and the thread and kind tags.
- Treat `needs_human` and `relay` as the table above describes.

### 3.2 `ack`

`data: {"ref_uuid"}`. An `ack` is a transport-level delivery confirmation. When the sender receives an `ack`, it moves `ref_uuid` from `sent` to `delivered`. A node never sends an `ack` for an `ack`.

- If a text stays un-acked (status `sent`) for `ack_timeout`, the sender sends the text again. `ack_timeout` is 5 minutes. The sender does this only while the contact is online.
- If a `sent` text is 24 h old, the sender stops and marks the text `failed`. The 24 h start when the sender creates the text.
- A `queued` text never fails. It waits until the contact connects.

### 3.3 `receipt`

`data: {"ref_uuid", "kind": "read" | "done"}`. A node sends a receipt only to peers whose `caps` include `receipt`. A receipt never wakes anyone.

- `read`: The message **was shown to a person or delivered into the context of an agent**. The sender marks `ref_uuid` as `read`. If the ack was lost, the sender also marks `ref_uuid` as `delivered`.
- `done`: The receiver **handled** the message. "Handled" has two meanings. The first meaning is that the receiver answered with `reply_to` set to that message. In this case, the receipt is automatic. The second meaning is that the receiver marked the message as handled explicitly (`toxi done`, MCP `mark_handled`).
- When the sender receives `done`, it marks the message as `handled`. `done` implies `read`. A later `read` never downgrades `handled`.
- If a node does not know `done`, it treats an incoming `done` as `read`.

For a multi-part message, `ref_uuid` is the part id. The sender marks every part. For a file, `ref_uuid` is the file `msg_uuid` of the sender (§3.7).

### 3.4 `profile`

```json
{"kind":"agent","name":"castle-op","runtime":"grok","model":"grok-4","host":"grok-vm",
 "operator":"jeff","role":"Power-Up-Chess ops bot","proto":2,"caps":["profile","session","receipt","part"]}
```

Each string is ≤120. `kind ∈ agent|human|service`. `caps` has ≤16 strings, and each string is ≤32.

- If a node has a card, it sends the card **each time a contact connects**. It also sends the card each time the card changes.
- The receiver keeps the latest card for each contact. The receiver uses `caps` for feature gating.
- At start-up, a node must refresh `proto` and `caps` from its own build. It must not take these values from a saved card.
- The card content is self-described. But the card travels inside the E2E channel. Thus only the key holder can send it.

Each capability name tells what the node can **receive and act on**. A declared capability never obliges the node to send the related field.

| cap | the node… | so a peer may… |
|---|---|---|
| `profile` | accepts and shows cards | send its card |
| `session` | stores and shows `from_session` | —. A node can always send optional fields. The receiver ignores unknown fields. |
| `receipt` | sends `read` and `done` receipts, and understands incoming receipts | expect `read` and `handled` status |
| `part` | reassembles `part` parts | send long texts as parts, not as `[i/n] ` pieces |

Only `part` and `receipt` gate the sending behaviour. `profile` and `session` are only informational.

### 3.5 `contact_share`

This type is unchanged from v1: `{"tox_id" (76 hex, required), "suggested_alias", "note" (may be empty)}`. The receiver never adds the contact automatically. The receiver files the share as a pending introduction. The user accepts it manually (`toxi accept-intro`).

### 3.6 `room`

```json
{"op":"create","id":"457707e9-…","name":"p3-room",
 "members":[{"pk":"8FBE…","name":"ada"},{"pk":"A6CD…","name":"belinda-bot"}]}
```

A **room card** is the full member list of an app-layer room. `id` and `name` are ≤64. `members` has ≤64 entries. In each entry, `pk` is 64 hex in uppercase, and `name` is ≤64.

Rules:

- A node accepts a card only from a contact.
- If the room already exists locally and its `created_by` is not the key of the sender, the node ignores the card. Only the creator changes the membership.
- If the own key of the node is not in `members`, the node deletes the room locally. This means that the creator removed the node. The history stays.
- In all other cases, the node upserts the room with `created_by` set to the key of the sender. The node **replaces** the member list.
- When a member connects, the creator sends the card to that member again. After `add` or `remove`, the creator sends the card to all members again.
- A room message is an ordinary `text`. Its `room` is the room id. Its `room_msg` is one new id for each logical message.
- The sender sends a copy of the same body to each member that is a contact of the sender.
- There is no transport group. If two members are not contacts of each other, they do not see the messages of each other. Clients should show this.
- **History for newcomers**:
  - When the card of the creator reaches a member for the first time, the creator relays the last 20 texts of the room to that member.
  - Each relayed text is an ordinary `text` envelope. It carries `relayed: {"name": <original writer>, "ts": <original time>}`, `room`, `room_msg` and the original thread, kind and tags.
  - `room_msg` is the original message id. The envelope `uuid` is also this id. Thus the receiver dedupes repeats.
  - The receiver stores a relayed text as **already read**. The receiver never notifies for it.
  - If a receiver does not know `relayed`, it sees an ordinary message from the creator.
  - If a history text is too long for one Tox message, the creator splits it into `part` parts. Each part has envelope `uuid` = `"<room_msg>:<i>"` and `part.id` = `room_msg`.
  - The creator splits in this way for all members, whatever their caps are. Thus a member without the `part` cap stores each part as a separate message.
- Display: A node shows its own fan-out copies with the same `room_msg` as one entry. The entry shows the status for each receiver.

### 3.7 `file`

```json
{"msg_uuid":"…","name":"shot.png","size":9085,"sha256":"…","from_session":"p3",
 "thread":"files","kind":"report"}
```

The sender sends this card **immediately before** `tox_file_send`. The transfer is a `TOX_FILE_KIND_DATA` transfer with the same `name` and `size`.

The receiver matches the card and the transfer on (friend, sanitized name, size) within 300 s. The card and the transfer can arrive **in either order**. After a match, the receiver does these steps:

- It stores the file with private permissions.
- It sets the id of the row to the `msg_uuid` of the sender. Thus receipts and `reply_to` refer to the same id.
- It copies the thread, kind and room fields and `from_session`.
- It classifies the file by extension (image / voice / video / file).
- It verifies `sha256`. If the hash does not match, the receiver keeps the file and adds the tag `sha256-mismatch`.

Files have no `ack`.

- When the transfer completes, the sender marks the file as `delivered`. The completion signal is a chunk request of length 0.
- If the receiver cancels the transfer, the file is `failed`.
- If the link drops, the file goes back into the queue. On reconnect, the transfer starts again from byte 0.

If a text body is over 64 KB, the sender sends it as a `.txt` file in this way. The sender does not send it as text.

## 4. Multi-part texts

Sender:

- If the full envelope is larger than 1372 bytes and the peer has the cap `part`, the sender splits `body` into parts. The sender splits on character boundaries.
- The sender sizes each part so that the envelope of each part fits. This size includes `from_session`, the thread fields and a worst-case `part`.
- The sender sends each part with its own `uuid` and with `part: {id, i, n}`. All parts use the same `id`.
- If the peer does not have the `part` cap, the sender sends independent texts with the prefix `[i/n] `. This is the v1 behaviour.
- The sender tracks the `ack` of each part separately. The logical message is `delivered` only when **every** part has an ack.
- When a `receipt` for the part `id` arrives, the logical message is `read`. This receipt marks all parts.

Receiver:

- The receiver buffers parts by (friend, `id`).
- The receiver sends an ack for **the uuid of each part**.
- One logical message counts once against the rate limit.
- When all `n` parts arrive, the receiver stores **one** message. Its `uuid` is `id`. Its body is the concatenation of the parts in `i` order. Its metadata comes from the first part that the receiver saw.
- After 60 s, the receiver stores the parts that arrived. It adds the prefix `[toxi: only k of n parts arrived] ` to the body.
- If a part arrives for an `id` that the receiver already stored, the receiver sends an ack and ignores the part. This case is a late resend.
- If the total is over 64 KB, the receiver drops the message and remembers the id.

Notification:

- The receiver notifies only when the message is complete. Notifications include hooks and wake-ups.

## 5. Status model (sender side)

`queued` → `sent` → `delivered` → `read` → `handled`

- `queued`: The contact is offline, or toxcore refused the message.
- `delivered`: An ack arrived, or the file transfer completed.
- `read`: A receipt arrived.
- `handled`: A `done` receipt arrived.
- `failed`: A `sent` text is still un-acked 24 h after its creation. A declined file transfer is also `failed`.
- A `queued` row never becomes `failed`.

A periodic sweep sends un-acked `sent` texts again after `ack_timeout`. The sweep does this only while the contact is online. A reconnect flushes only `queued` rows and owed receipts. The reconnect flushes the `queued` rows in send order. For the queue decision, the sender asks toxcore for the presence at send time. The sender does not use a cached flag.

A node routes replies locally. This is node behaviour, not wire format. The node applies these rules in this order:

1. If a text starts with `@name`, `@name@me` or `name@me`, the node delivers it only to the session with the name `name`. `me` is the name on the card of the node. The name is `TOXI_SESSION`, or else the name of the project directory. If another live session has that name, the node adds `-2`, `-3` and so on. The match is case-insensitive.
2. Otherwise, the node uses its record of which of its own sessions sent each message. This record never goes on the wire. The node delivers an incoming `reply_to` to that session, or to the session that wrote last in the same thread with that contact. This link is valid for 30 minutes from the `created_at` of the incoming message.
3. After 30 minutes, or if no such link exists, the node applies the configured binding of the peer.
4. If no live session matches, or more than one session matches, the message stays in the shared inbox. No session gets it automatically.

The `from_session` of an outgoing message is the name of the sending session. A peer that wants to address that session can start its text with `@` and that name.

## 6. Limits and abuse resistance

| item | limit |
|---|---|
| one Tox message | 1372 bytes |
| text body after reassembly | 64 KB. A bigger body goes as a file. |
| file | 100 MB |
| incoming texts per contact | 60 / minute. The parts of one message count once. The receiver drops extra texts **without an ack**. |
| incoming friend requests | 5 / minute |
| part reassembly window | 60 s. The file-meta window is 300 s. |
| strings | See the limit of each field. Truncate a long string. Do not reject it. |

Non-friends cannot send at all. Tox enforces this. Keys, databases, config, cards and received files are private files (0600 / 0700).

## 7. Pairing (optional for clients)

`toxi pair` creates an invite. The invite is the base64url of `{"v":1,"tox_id":<76 hex>,"name":<inviter name>,"nonce":<32 hex>,"exp":<unix>}`. The invite is valid for 10 minutes. The invite is single use.

- The joiner adds the Tox ID of the inviter with a friend request. The friend-request message is `{"toxi":"toxi-invite","nonce":…,"name":<joiner name>}`.
- If a request carries a live nonce, the inviter accepts it automatically. The inviter names the contact after `name`.
- All other friend requests stay manual.
- A mobile client can show the invite as a QR code. A mobile client can join when it scans an invite QR code.

## 8. Compatibility matrix

| peer | what it understands | what a v2 node does for it |
|---|---|---|
| v1 (ToxiOS today, toxi ≤0.3.0) | `text` (body only), `ack`, `contact_share` | It does not send `profile`, `receipt`, `room` or `file`. It sends long texts as `[i/n] ` pieces. The peer ignores extra text fields. |
| v2 without a cap | what its `caps` declare | It applies feature gating for each cap (`part`, `receipt`). |
| v2 full | everything above | — |

A v2 node must continue to work with a v1 peer indefinitely. A v1 peer loses only the new features. A v1 peer never loses messages.
