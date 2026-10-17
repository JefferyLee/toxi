# Adapter: ToxiOS (iPhone)

> 中文版 / Chinese version: ios.zh-CN.md

ToxiOS is a separate repository: `~/Workplace/ToxiOS`. It has two parts:

- A Go daemon, `toxid`, built on CGo c-toxcore. It keeps the identity online on a Mac or a server.
- A SwiftUI app that talks to the daemon.

For toxi, ToxiOS is a *person* adapter. Its card is `kind = human`, `runtime = toxios`.

Status: ToxiOS implements protocol **v2**. This includes profile cards, session labels, part reassembly, thread and kind display, read receipts, files with sha256 metadata, rooms and relayed room history. ToxiOS was verified against Ada. For the wire format, see [../protocol.md](../protocol.md).

## Your phone as the owner

To talk to your agents from the phone as yourself, declare the contact of the phone as an owner peer. Do this in the `config.toml` of each agent identity:

```toml
[peers.jeff-iphone]
trust = "owner"
dirs  = ["/path/to/project"]   # the session that gets your messages by default
```

The agent then treats each message as if you typed it in that session. The agent answers on the phone. To address a different session, start the message with `@name`. `name` is the `TOXI_SESSION` of that session, or else the name of its project directory. If you lose the device, see [../security.md](../security.md).
