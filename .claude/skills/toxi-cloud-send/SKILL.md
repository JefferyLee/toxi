---
name: toxi-cloud-send
description: Send a message from this Claude Code cloud session to one of the user's local Claude Code sessions (through the toxi cloud relay). Use when the user asks you to tell, notify, hand off to or report to a local session or "Ada", or when a task says to report back to a local session.
---

# Send a message to a local session

This cloud session cannot run toxi. A relay takes your message to the user's
machine, and toxi gives it to the local session that you name.

```bash
curl -sS -X POST "$TOXI_RELAY_URL/send" \
  -H 'Content-Type: application/json' \
  ${TOXI_RELAY_TOKEN:+-H "Authorization: Bearer $TOXI_RELAY_TOKEN"} \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"to": sys.argv[1], "from": sys.argv[2], "body": sys.argv[3]}))' \
        "<local session name>" "<short name for this cloud session>" "<message>")"
```

- `to`: the name of the local session, for example `toxi` or `ones-mind` (the
  project directory name). Leave it out to put the message in the shared inbox.
- `from`: a short name for this session, so the reader knows who wrote.
- `body`: 1 to 8000 characters.
- The answer `{"queued":true}` means the relay has the message. The local
  session gets it when it next works (seconds if it is busy, at its next turn
  if it is idle).
- The relay is one-way: the local session cannot answer you through it.
- If `TOXI_RELAY_URL` is not set, tell the user that the cloud environment has
  no relay configured; do not look for another way to reach their machine.
- The proxy adds the credential for the relay host. Do not print, log or send
  any token.
