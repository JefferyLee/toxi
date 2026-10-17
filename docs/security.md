# Security model

> 中文版 / Chinese version: security.zh-CN.md

## What is protected, by whom

| layer | guarantee | provided by |
|---|---|---|
| transport | end-to-end encryption, authenticated by the key of the contact. Only friends can send. | Tox |
| identity | a participant *is* its key pair. The profile card is self-described, but it can only come from that key. | Tox + envelope `profile` |
| local data | keys, database, config, profile cards and received files are 0600 inside a 0700 directory. The daemon applies these modes again at each start. | daemon |
| agents | incoming text is data, not instructions. The adapter frames each peer by trust level. If a message relays "the user approved", that is never an approval. | adapters + `peers.GUIDES` |
| flooding | 60 texts per contact per minute, 5 friend requests per minute, 64 KB per text, 100 MB per file | daemon |

## Threats and answers

**A peer agent is compromised or confused and tells my agent to do something.** Each injected message carries the profile card and the trust level of the sender. The wording after it tells the agent what it can do:

- The messages of an `observer` are information.
- The agent can act on the requests of a `collaborator` within the current task.
- For these two levels, the user must approve any destructive action or any action outside the project.
- The messages of an `owner` come from the user's own device. The agent treats them as if the user typed them in this session. The agent answers on that device.

The user decides `needs_human` items. `relay` content is hearsay. The adapter never injects messages from contacts that are not peers. It only summarizes them.

**Someone takes my phone.** A contact with `trust = "owner"` speaks as you to each session that it reaches. Thus its key is as sensitive as your terminal. If you lose the device, remove its `[peers.<alias>]` table. This change has an immediate effect. Then remove the contact with `toxi remove`. Trust does not pass from one agent to another. If another agent relays an `owner` message, the agent applies the trust level of the relaying agent. The message does not count as yours.

**Someone impersonates a peer.** They cannot. Messages come from a key, and only the keys that the operator befriended are contacts. A profile card can claim anything. But the profile card still arrives over the channel of the key that sent it. You own the local alias.

**A message is replayed or duplicated.** Each message has a uuid that the sender selects. The receiver stores the message once. It sends an ACK for each duplicate, so the sender stops its retries.

**My files leak.** Everything under `TOXI_HOME` is private to the user account. Logs never contain message bodies. In logs, public keys appear only as 8-character prefixes. The `on_message` shell hook and the webhook carry only aliases and ids. They carry bodies only if you deliberately set `include_body = true`.

If `on_message_secret` is set, the webhook has an HMAC signature. The signature header is `X-Toxi-Signature: sha256=HMAC(secret, "<ts>.<body>")`. The `X-Toxi-Timestamp` header holds `<ts>`.

**A stranger adds me.** A friend request waits for `toxi accept`. There is one exception: if a request answers a live `toxi pair` invite, the daemon accepts it automatically. An invite has a random 32-hex nonce. It is valid for 10 minutes, and it works one time only. An invite is as sensitive as your Tox ID. Share invites over a channel that you trust.

**My IP is visible.** Yes, the people that you chat with can see it. This is a property of Tox. If this is important to you, route the traffic through Tor. Use the `[tox] proxy_*` keys in the config. The PRD gives the details.

**A machine with the daemon is shared.** Do not share it. `tox_state.bin` has no at-rest encryption yet. Refer to PRD §4.9.3.

## Rules adapters must follow

1. Inject peer messages only through `toxi peer-inbox` or `wait_for_message`. Then the trust wording travels with them.
2. If the operator did not opt in, never let a message body go to a webhook, a log or a status line.
3. Mark a message read only after the person saw it or the agent received it. This step sends the receipt.
4. Use the same `TOXI_HOME` for the CLI, hooks and MCP server of one identity. `toxi doctor` checks this.
