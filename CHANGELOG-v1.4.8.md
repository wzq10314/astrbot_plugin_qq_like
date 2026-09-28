# v1.4.8 Pixiv 查询连接恢复

## 问题

长时间空闲后首次 `/pixiv` 查询报 `SSL: UNEXPECTED_EOF_WHILE_READING`，再次发送却成功。这说明请求过程中 TLS 连接被提前关闭；结合重试后恢复的表现，可能与失效的复用连接或代理短暂断连有关，单凭该日志无法确认是代理、网络还是上游主动断开。

原代码只有 OAuth 认证有重试，普通搜索直接传播网络异常。

## 修改

- 在共享 Pixiv 客户端的请求层处理 TLS EOF、连接重置和无响应断连；只读 GET 请求清理旧的直连/代理连接池后自动重试一次。
- 继续使用同一个 API 客户端和会话，保留 Token、代理、反代地址、附加请求头、超时及 TLS 证书校验配置。
- 同时覆盖 `/pixiv` 命令和使用该客户端的自然语言查询；重试发生在获取查询结果阶段，不重复发送图片。
- 登录/刷新认证、收藏等 POST 操作不使用此新增重试；流式文件下载、证书校验失败、HTTP 状态错误、DNS 错误和超时也不因此重复请求。原有 OAuth 重试策略保持不变。
- 持续断连最多尝试两次，然后返回简短的网络故障提示；不无限等待或强制重试。
- 保留 v1.4.7 的 JM/PICA 自然语言功能、章节下载、帮助图片及自动安装依赖；不新增依赖或配置项。

## 使用

上传此版 ZIP 更新插件并重载。无需重新填写 Pixiv Token，也无需修改原代理设置。如果网络连续失败，自动重连仍可能失败，需要查看实际网络或代理状态。

实现依据：[Requests 连接池清理](https://requests.readthedocs.io/en/latest/_modules/requests/adapters/)、[urllib3 PoolManager.clear 行为](https://urllib3.readthedocs.io/en/v2.0.5/reference/urllib3.poolmanager.html)。
