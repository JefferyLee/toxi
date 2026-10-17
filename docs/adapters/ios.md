# Adapter: ToxiOS (iPhone)

> 中文版 / Chinese version: ios.zh-CN.md

ToxiOS is a separate repository (`~/Workplace/ToxiOS`): a Go daemon (`toxid`, CGo c-toxcore) that keeps the identity online on a Mac or server, and a SwiftUI app that talks to it. From toxi's point of view it is a *person* adapter: the card is `kind = human`, `runtime = toxios`.

Status: ToxiOS implements protocol **v2** — profile cards, session labels, part reassembly, thread/kind display, read receipts, files with sha256 metadata, rooms and relayed room history — and was verified against Ada. See [../protocol.md](../protocol.md) for the wire format.

## Your phone as the owner

To talk to your agents from the phone as yourself, declare the phone's contact as an owner peer in each agent identity's `config.toml`:

```toml
[peers.jeff-iphone]
trust = "owner"
dirs  = ["/path/to/project"]   # the session that gets your messages by default
```

The agent then treats each message as if you had typed it in that session and answers on the phone. Start a message with `@name` to address another session instead (its `TOXI_SESSION`, else its project dir name). See [../security.md](../security.md) for a lost device.
