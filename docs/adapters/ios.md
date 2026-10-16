# Adapter: ToxiOS (iPhone)

> 中文版 / Chinese version: ios.zh-CN.md

ToxiOS is a separate repository (`~/Workplace/ToxiOS`): a Go daemon (`toxid`, CGo c-toxcore) that keeps the identity online on a Mac or server, and a SwiftUI app that talks to it. From toxi's point of view it is a *person* adapter: the card is `kind = human`, `runtime = toxios`.

Status: ToxiOS speaks envelope **v1** today (text, ack, contact_share). The v2 upgrade — profile cards, read receipts, part reassembly, thread/kind display, rooms, file metadata, QR pairing — is specified in [../protocol.md](../protocol.md) and tracked in the ToxiOS repository (decision 6 of the design). Until then a v2 node treats it as a v1 peer: long texts arrive as `[i/n] ` pieces, room messages as plain messages, and no receipts come back.
