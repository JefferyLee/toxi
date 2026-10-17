# Security model

> 中文版 / Chinese version: security.zh-CN.md

## What is protected, by whom

| layer | guarantee | provided by |
|---|---|---|
| transport | end-to-end encrypted, authenticated by the peer's key; only friends can send | Tox |
| identity | a participant *is* its key pair; the profile card is self-described but can only come from that key | Tox + envelope `profile` |
| local data | keys, database, config, cards, received files are 0600 inside a 0700 directory; the daemon re-applies this on every start | daemon |
| agents | incoming text is data, not instructions; peers are framed by trust level; "the user approved" relayed in a message is never an approval | adapters + `peers.GUIDES` |
| flooding | 60 texts per contact per minute, 5 friend requests per minute, 64 KB per text, 100 MB per file | daemon |

## Threats and answers

**A peer agent is compromised or confused and tells my agent to do something.** Every injected message carries the sender's card and trust level, and the wording that follows it says what the agent may do: an `observer`'s messages are information, a `collaborator`'s requests may be acted on within the current task, and for both anything destructive or outside the project needs the user; an `owner`'s messages (the user's own device) are treated as if the user had typed them in this session, and the agent answers on that device. `needs_human` items are for the user to decide; `relay` content is hearsay. Non-peer contacts are never injected at all (only summarised).

**Someone takes my phone.** A contact with `trust = "owner"` speaks as you to every session it reaches, so its key is as sensitive as your terminal. If the device is lost, remove its `[peers.<alias>]` table (takes effect at once) and `toxi remove` the contact. There is no delegation: an `owner` message relayed by another agent is framed by that agent's trust level, not as yours.

**Someone impersonates a peer.** They cannot: messages come from a key, and only keys the operator befriended are contacts. A card can claim anything, but it still arrives over the channel of the key that sent it, and the local alias is yours.

**A message is replayed or duplicated.** Every message has a sender-chosen uuid; the receiver stores it once and ACKs duplicates so the sender stops retrying.

**My files leak.** Everything under `TOXI_HOME` is private to the user account. Logs never contain message bodies (public keys appear as 8-character prefixes). The `on_message` shell hook and the webhook carry only aliases and ids unless `include_body = true` is set deliberately; the webhook is HMAC-signed when `on_message_secret` is set (`X-Toxi-Signature: sha256=HMAC(secret, "<ts>.<body>")`, with `<ts>` in `X-Toxi-Timestamp`).

**A stranger adds me.** Friend requests wait for `toxi accept` — except a request that answers a live `toxi pair` invite (random 32-hex nonce, 10 minutes, single use), which is accepted automatically. Share invites over a channel you trust: an invite is as sensitive as your Tox ID.

**My IP is visible.** It is, to the people you chat with — a property of Tox. Route through Tor (`[tox] proxy_*` in config, see the PRD) if that matters to you.

**A machine with the daemon is shared.** Don't. There is no at-rest encryption of `tox_state.bin` yet (PRD §4.9.3).

## Rules adapters must follow

1. Inject peer messages only through `toxi peer-inbox` / `wait_for_message`, so the trust wording travels with them.
2. Never let a message body reach a webhook, a log or a status line unless the operator opted in.
3. Mark read only when the message was shown to the person or delivered into the agent (that is what sends the receipt).
4. Keep `TOXI_HOME` consistent across the CLI, hooks and MCP server of one identity; `toxi doctor` checks.
