# 适配器：ToxiOS（iPhone）

> English version: ios.md

ToxiOS 是一个独立仓库（`~/Workplace/ToxiOS`）：一个 Go daemon（`toxid`，CGo c-toxcore）在 Mac 或服务器上让身份保持在线，再加一个与之通信的 SwiftUI app。从 toxi 的角度看它是一个*人*的适配器：卡片为 `kind = human`、`runtime = toxios`。

状态：ToxiOS 已实现协议 **v2** —— 身份卡片、会话标签、分片重组、线程/类型显示、已读回执、带 sha256 元信息的文件、room 与 room 历史补发 —— 并已对 Ada 验证过。线上格式见 [../protocol.zh-CN.md](../protocol.zh-CN.md)。

## 把手机设为 owner

要以你本人的身份从手机和 agent 对话，在每个 agent 身份的 `config.toml` 里把手机这个联系人声明为 owner peer：

```toml
[peers.jeff-iphone]
trust = "owner"
dirs  = ["/path/to/project"]   # 默认接收你消息的会话
```

agent 随后把每条消息当作你在那个会话里亲手输入的，并在手机上回答。消息以 `@name` 开头则改投另一个会话（它的 `TOXI_SESSION`，否则为项目目录名）。设备丢失时的处理见 [../security.zh-CN.md](../security.zh-CN.md)。
