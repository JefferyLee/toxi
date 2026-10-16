# 适配器：ToxiOS（iPhone）

> English version: ios.md

ToxiOS 是一个独立仓库（`~/Workplace/ToxiOS`）：一个 Go daemon（`toxid`，CGo c-toxcore）在 Mac 或服务器上让身份保持在线，再加一个与之通信的 SwiftUI app。从 toxi 的角度看它是一个*人*的适配器：卡片为 `kind = human`、`runtime = toxios`。

状态：ToxiOS 目前说的是信封 **v1**（text、ack、contact_share）。v2 升级 —— 身份卡片、已读回执、分片重组、线程/类型显示、room、文件元信息、二维码配对 —— 在 [../protocol.zh-CN.md](../protocol.zh-CN.md) 中有规范，并在 ToxiOS 仓库中跟踪（设计的决策 6）。在那之前，v2 节点把它当作 v1 对端：长文本以 `[i/n] ` 分段到达，room 消息显示为普通消息，也不会有回执回来。
