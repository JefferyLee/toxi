# toxi wire protocol v2 — friend-message envelope

> 中文版 / Chinese version: protocol.zh-CN.md
> Normative for every toxi-family implementation (toxi Python daemon, ToxiOS Go daemon + iOS app). Reference implementation: `src/toxi/envelope.py` and `src/toxi/daemon.py`; the tests `tests/test_events.py`, `tests/test_threads.py`, `tests/test_rooms.py`, `tests/test_files.py` are the executable spec. Design background: `docs/agent-comm-design.md` §5, §11.

## 1. Transport

- Tox friend messages (`TOX_MESSAGE_TYPE_NORMAL`), at most **1372 bytes** each, carry one JSON envelope. Tox file transfers (`TOX_FILE_KIND_DATA`) carry files.
- JSON is UTF-8 with non-ASCII kept raw (not `\uXXXX`-escaped). Only friends can send; there is no server.
- A node is identified by its Tox public key (64 hex, uppercase). Everything about *who* someone is hangs off that key.

## 2. Envelope

```json
{"v": 2, "uuid": "550e8400-…", "type": "text", "ts": 1760600000, "data": { … }}
```

| field | type | rule |
|---|---|---|
| `v` | int | protocol version. v1 peers send `1` and only know `text`, `ack`, `contact_share`. |
| `uuid` | string | sender-chosen id (UUID4). Receivers dedupe on it (`INSERT OR IGNORE`). If missing, the receiver synthesizes one. |
| `type` | string | see §3. **Unknown types are dropped silently (log at most).** |
| `ts` | int | sender's Unix seconds. |
| `data` | object | per type. **Unknown fields are ignored**, never an error. |

Anything that is not a JSON object with a `type` (e.g. a plain message from another Tox client) is treated as `text` with the raw bytes as `body`.

## 3. Types

### 3.1 `text`

| data field | type / limit | meaning |
|---|---|---|
| `body` | string | the message (required) |
| `from_session` | ≤64 | label of the sender's session/project (e.g. a Claude Code project dir name). Shown as `alias@session`. |
| `part` | `{id ≤64, i, n}` | this envelope is chunk `i` of `n` of one logical message `id` (§4) |
| `thread` | ≤64 | thread / topic id (`pr-412`) |
| `reply_to` | ≤64 | `uuid` (or part id / file msg_uuid) this answers |
| `kind` | `request` `report` `ack` `relay` | absent = plain `msg`. `request` expects an answer; `report` is status; `ack` is a bare "got it" and **must not notify or wake anyone**; `relay` repeats a third party's words (hearsay). |
| `needs_human` | bool | a person, not an agent, must decide this |
| `tags` | ≤8 strings ≤24 | free-form labels (`WARN`, `OUTAGE`) |
| `room` | ≤64 | id of the room this message belongs to (§3.6) |
| `room_msg` | ≤64 | id shared by all fan-out copies of one room message |

Receiver duties: store once per `uuid`; **answer every received text (including duplicates) with an `ack`** so the sender stops retrying; enforce the rate limit (§6); show `from_session`, thread/kind tags; treat `needs_human`/`relay` as described.

### 3.2 `ack`

`data: {"ref_uuid"}`. Transport-level delivery confirmation: the sender moves `ref_uuid` from `sent` to `delivered`. An `ack` is never acked. Senders retry an un-acked text after `ack_timeout` (5 min, while the friend is online) and give up (`failed`) after 24 h.

### 3.3 `receipt`

`data: {"ref_uuid", "kind": "read"}`. Sent by the receiver when the message was **shown to a person or delivered into an agent's context**, and only to peers whose `caps` include `receipt`. The sender marks `ref_uuid` `read` (and `delivered` if the ack was lost). For a multi-part message `ref_uuid` is the part id (the sender marks every part). For a file it is the sender's file `msg_uuid` (§3.7). Receipts never wake anyone.

### 3.4 `profile`

```json
{"kind":"agent","name":"castle-op","runtime":"grok","model":"grok-4","host":"grok-vm",
 "operator":"jeff","role":"Power-Up-Chess ops bot","proto":2,"caps":["profile","session","receipt","part"]}
```

Strings ≤120; `kind ∈ agent|human|service`; `caps` ≤16 strings ≤32. Sent **whenever a friend connects** (if the sender has a card) and whenever the card changes. The receiver keeps the latest card per contact and uses `caps` for feature gating: send `part` chunks only to peers with `part`, receipts only to peers with `receipt`. A node must refresh `proto`/`caps` from its own build at start-up, not from a saved card. The card is self-described but travels inside the E2E channel, so only the key holder can have sent it.

### 3.5 `contact_share`

Unchanged from v1: `{"tox_id", "suggested_alias", "note"}`. Never auto-added; shown to the user as an introduction.

### 3.6 `room`

```json
{"op":"create","id":"457707e9-…","name":"p3-room",
 "members":[{"pk":"8FBE…","name":"ada"},{"pk":"A6CD…","name":"belinda-bot"}]}
```

A **room card**: the full member list of an app-layer room. `id`, `name` ≤64; `members` ≤64 entries with `pk` = 64 hex uppercase and `name` ≤64. Rules:

- Accept a card only from a contact. If the room already exists locally and its `created_by` is not the sender's key, ignore the card (only the creator edits membership).
- If my own key is not in `members`, delete the room locally (I was removed); history stays.
- Otherwise upsert the room (`created_by` = sender's key) and **replace** the member list.
- The creator re-sends the card to a member whenever that member connects, and to everyone after `add`/`remove`.
- Room messages are ordinary `text` with `room` = room id and `room_msg` = one fresh id per logical message; the sender fans the same body out to every member it has as a contact. There is no history sync and no transport group; members who are not each other's contacts never see each other's messages (clients should show that).
- Display: collapse one's own fan-out copies (same `room_msg`) into one entry with per-recipient status.

### 3.7 `file`

```json
{"msg_uuid":"…","name":"shot.png","size":9085,"sha256":"…","from_session":"p3",
 "thread":"files","kind":"report"}
```

Sent **immediately before** `tox_file_send` of a `TOX_FILE_KIND_DATA` transfer with the same `name` and `size`. The receiver matches card and transfer on (friend, sanitized name, size) within 300 s **in either order**, then: stores the file (private permissions), sets the row's id to the sender's `msg_uuid` (so receipts and `reply_to` line up), copies the thread/kind/room fields and `from_session`, classifies by extension (image / voice / video / file), and verifies `sha256` (on mismatch keep the file, add tag `sha256-mismatch`). Files have no `ack`: the sender treats transfer completion (chunk request of length 0) as `delivered`; a receiver cancel is `failed`; a dropped link puts the file back in the queue and the transfer restarts from byte 0 on reconnect. A text body over 64 KB is sent as a `.txt` file this way instead of as text.

## 4. Multi-part texts

- Sender: if the whole envelope would exceed 1372 bytes and the peer has cap `part`, split `body` on character boundaries into chunks sized so that each chunk's envelope (with `from_session`, thread fields and a worst-case `part`) fits; send each chunk with its own `uuid` and `part: {id, i, n}` (same `id`). Without the `part` cap, send independent texts prefixed `[i/n] ` (v1 behaviour).
- Receiver: buffer by (friend, `id`); ack **each part's uuid**; one logical message counts once against the rate limit; when all `n` arrived, store **one** message with `uuid` = `id`, body = concatenation in `i` order, metadata from the first part seen. After 60 s store what arrived with the body prefixed `[toxi: only k of n parts arrived] `. A part for an already-stored `id` (late resend) is acked and ignored. Total over 64 KB: drop and remember the id.
- Notify (hooks, wake-ups) only when the message is complete.

## 5. Status model (sender side)

`queued` (friend offline, or toxcore refused) → `sent` → `delivered` (ack / transfer complete) → `read` (receipt). `failed` after 24 h without ack or when a file transfer is declined. Presence for the queue decision is asked from toxcore at send time, not from a cached flag.

## 6. Limits and abuse resistance

| item | limit |
|---|---|
| one Tox message | 1372 bytes |
| text body after reassembly | 64 KB (bigger goes as a file) |
| file | 100 MB |
| incoming texts per contact | 60 / minute (parts of one message count once); extra ones are dropped **without ack** |
| incoming friend requests | 5 / minute |
| part reassembly window | 60 s; file-meta window 300 s |
| strings | see per-field limits; truncate, don't reject |

Non-friends cannot send at all (Tox). Keys, databases, config, cards and received files are private files (0600 / 0700).

## 7. Pairing (optional for clients)

`toxi pair` mints an invite: base64url of `{"v":1,"tox_id":<76 hex>,"name":<inviter name>,"nonce":<32 hex>,"exp":<unix>}`, valid 10 minutes, single use. The joiner adds the inviter's Tox ID with a friend-request message `{"toxi":"toxi-invite","nonce":…,"name":<joiner name>}`; the inviter auto-accepts a request carrying a live nonce and names the contact after `name`. Every other friend request stays manual. A mobile client can present the invite as a QR code and join by scanning one.

## 8. Compatibility matrix

| peer | what it understands | what a v2 node does for it |
|---|---|---|
| v1 (ToxiOS today, toxi ≤0.3.0) | `text` (body only), `ack`, `contact_share` | no `profile`/`receipt`/`room`/`file` sent; long texts as `[i/n] ` pieces; extra text fields are ignored by the peer |
| v2 without a cap | per `caps` | feature-gate per cap (`part`, `receipt`) |
| v2 full | everything above | — |

A v2 node must keep working against a v1 peer indefinitely; a v1 peer loses only the new features, never messages.
