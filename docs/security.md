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

**A peer writes text that looks like toxi's own lines.** For example, a body that contains a line `[toxi] … (trust: owner)`. toxi puts each field that a sender chooses (session name, card fields, thread, tags) on one line. It indents each line of a body. It puts the messages of each group between two marker lines with a random tag. Thus no line from a sender starts at column 0, and a sender cannot know the tag. The trust level comes from your config, never from the message. A long text points to `toxi show <id>` only if the id is a plain id.

**An agent is talked into sending my files.** The daemon sends a file only from these places: the toxi media dir, the temp dir, the project dir of a running session (not your home dir), or a dir in `[files] send_dirs`. It never sends a file from `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config`, `~/.claude`, `~/.codex`, `~/Library` or similar places, and never a `.env`, `.netrc` or similar file. A person who runs `toxi send --file` at a terminal can also send other files. A script that runs outside an agent session (a headless bot) can send the files below its working dir. This rule stops the one-step path: a misled agent that sends a secret file directly. It does not stop an agent that first copies a secret into the temp dir or into a message body. It does not stop other programs of your user account, because they can talk to the daemon directly.

**Someone gets the alias of a peer.** Trust belongs to an alias. A contact that joins with an invite or an introduction never gets an alias that a `[peers.<alias>]` table holds. To bind a table to one key, add `key = "<64 hex public key>"`. Then no other key can get that alias. When you remove or rename a contact, also remove or rename its table. `toxi remove` warns you, and `toxi doctor` reports a table that has no contact.

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
3. Mark a message read only after the person saw it or the agent received it. This step sends the receipt. A hook first reads the messages without a mark (`toxi peer-inbox --peek`), prints them, and then marks them (`toxi mark-read`). If the hook stops between these steps, the messages come again.
4. Use the same `TOXI_HOME` for the CLI, hooks and MCP server of one identity. `toxi doctor` checks this.
5. Do not deliver peer messages to a subagent. The main session answers them.
