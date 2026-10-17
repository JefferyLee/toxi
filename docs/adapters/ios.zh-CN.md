# 适配器：ToxiOS（iPhone）

> English version: ios.md

ToxiOS 是一个独立仓库：`~/Workplace/ToxiOS`。它有两部分：

- 一个 Go daemon `toxid`，基于 CGo c-toxcore。它在 Mac 或服务器上让身份保持在线。
- 一个与 daemon 通信的 SwiftUI app。

从 toxi 的角度看，ToxiOS 是一个*人*的适配器。它的卡片为 `kind = human`、`runtime = toxios`。

状态：ToxiOS 已实现协议 **v2**。这包括身份卡片、会话标签、分片重组、线程和类型显示、已读回执、带 sha256 元信息的文件、room 和 room 历史补发。ToxiOS 已对 Ada 验证过。线格式见 [../protocol.zh-CN.md](../protocol.zh-CN.md)。

## 把手机设为 owner

要以你本人的身份从手机和 agent 对话，把手机这个联系人声明为 owner peer。在每个 agent 身份的 `config.toml` 里做这一步：

```toml
[peers.jeff-iphone]
trust = "owner"
dirs  = ["/path/to/project"]   # 默认接收你消息的会话
```

agent 随后把每条消息当作你在那个会话里亲手输入的。agent 在手机上回答。要改投另一个会话，让消息以 `@name` 开头。`name` 是那个会话的 `TOXI_SESSION`，否则是它的项目目录名。设备丢失时，见 [../security.zh-CN.md](../security.zh-CN.md)。
