# 可选漫画阅读页与 Pixiv 原图页

这个服务由你自己部署，与 AstrBot 分开运行。未部署时继续使用普通 QQ 回复；不影响点赞和状态图。网页是持链接访问模式，没有 QQ 登录校验，请只用于自己有权保存、分享的非色情内容。

## 需要准备什么

- 自己的 HTTPS 域名和反向代理。
- 服务器上已经安装本插件，且知道 AstrBot 数据目录在宿主机的路径。
- Docker 网络 `astrbot-net`：AstrBot、阅读服务和反向代理须能互通。若你的网络名称不同，请相应修改示例。

服务不需要 Node.js 或额外 Python 包。以下示例在服务器执行，`/root/data` 应替换成你实际挂载到 `/AstrBot/data` 的宿主机目录。

## Docker 部署

1. 进入仓库的 `reader` 目录，生成配置，域名换成你自己的：

```bash
python3 configure.py --public-base https://example.com/reader
```

会生成两个包含同一随机密钥的私有文件。不要发到聊天或提交到 GitHub。

2. 把客户端配置放到插件数据目录：

```bash
install -m 600 reader-client.json /root/data/plugin_data/astrbot_plugin_qq_like/reader-client.json
```

3. 启动服务：

```bash
ASTRBOT_DATA_DIR=/root/data docker compose up -d
```

Compose 没有发布宿主机端口。内部 API 仅供插件创建阅读页，公网只需反向代理 `/reader/*`。

4. 在已有 Caddy 域名块中、其它通配处理之前加入：

```caddyfile
handle /reader/* {
    @notRead not method GET HEAD
    respond @notRead 405
    reverse_proxy qq-like-reader:8912
}
```

不要将 `/api/collections` 暴露到公网。Caddy 应与阅读服务在同一 Docker 网络。使用 Nginx 时也保留 `/reader/` 路径，代理到同一内部服务。

5. 重载插件并测试。漫画 ZIP 下载完成后返回阅读链接；Pixiv 保留 QQ 图片回复，稍后追加原图链接。Pixiv 原图网页仅收录 API 标记为全年龄的静态作品。

## 目录与配置

服务端 `config.json`：`source_dirs` 是服务容器所见的来源目录，`state_dir` 存放阅读快照，`public_base` 是你的公开 HTTPS 地址，`ttl_seconds` 默认 604800（7 天）。

插件端 `reader-client.json`：`endpoint` 是 AstrBot 可以访问的内部创建接口，`public_base` 必须与服务端一致，`api_key` 必须与服务端密钥一致。非 Docker 部署请自行替换地址和路径。

从早期私有部署升级时，为已有客户端配置补上 `public_base`，保留原来的 `endpoint` 和 `api_key`，不要重新生成密钥。阅读服务源码需同步更新后重启。

## 限制与故障排查

- ZIP 必须未加密、包含有效图片且不超过 400 MiB，单页不超过 20 MiB；Pixiv 合集下载预算 350 MiB。
- 网页保留文件原始字节，不重新编码；显示尺寸适配屏幕。下载失败的原图会提示缺失，不用预览图替代。
- 链接默认 7 天有效；任何持链接的人都能访问。服务没有公开列表，不会发布搜索历史或用户凭据。
- QQ 原图页生成失败不影响原本回复。检查 `docker compose logs --tail 50`，确认来源目录挂载、内部网络、密钥和 `public_base` 一致。
- 快照有总容量限制，过期快照会清理；Pica 自身下载缓存由插件的缓存设置管理。
