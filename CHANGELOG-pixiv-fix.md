# v1.4.4 Pixiv 原图链接修复

## 使用效果

保留合并转发。每个图片节点中包含预览图片和可复制的下载链接；普通发图也使用同一逻辑。
下载链接优先使用 API 的 `original` / `original_image_url`，不受 `image_quality`、PIL 压缩或 QQ 预览压缩影响。
多页作品逐页匹配链接，关闭 `show_details` 后也保留下载链接。预览下载失败时仍返回链接。
这次增加的是每张图片的直接下载链接，不是一个汇总全部图片的网页或 ZIP 下载站。

## 修改文件

- `pixiv_reborn/utils/pixiv_utils.py`：统一原图地址提取、反代处理、普通消息和合并转发的图片构建；合并转发遵循 URL/文件/字节配置；单条合并模式按展开后的页数计算批次；动图附 API 帧包和可用的原始静帧地址，转换失败保留下载入口。
- `pixiv_reborn/handlers/fanbox.py`：在线图片及失败提示附处理后的链接；本地下载回看读取保存的源地址。旧缓存没有源地址时明确提示，不伪造原图链接。
- `pixiv_reborn/fanbox/downloader.py`：新下载记录 `image_sources.json`，按文件名保存原图来源，供回看时使用。
- `_conf_schema.json`：说明预览画质与原图链接相互独立，以及反代配置的格式。
- `pixiv_reborn/data/helpmsg.json`、`README.md`：同步使用说明。
- `tests/pixiv_reborn/test_image_links.py`：新增 26 项参数化回归用例。
- `scripts/build_zip.py`：生成带 `pixiv-fix` 标识的安装包。
- `CHANGELOG-pixiv-fix.md`：本说明。

## 配置与边界

- 沿用 `use_image_proxy` 和 `image_proxy_host`。默认启用反代；域名或完整 HTTP(S) 地址均可，可带路径前缀。配置普通 HTTP 代理时也不再跳过图片反代。
- 明确关闭 `use_image_proxy` 时仍尊重配置，返回直连地址。需要代理链接请保持开启，并填入可用的反代服务。
- 仅替换 Pixiv 图片域名 `i.pximg.net`，保留原图路径、扩展名和查询参数；已经处理的地址不会二次改写。Fanbox / Nekohouse 图片也经过统一入口，但不会被错误替换成只支持 pximg 的反代域名。
- 若 API 未提供原图，明确标注“API未提供原图”，不把缩略图冒充原图。Fanbox 列表封面仅能提供接口给出的图片地址。
- 动图提供 API 返回的帧包地址，不承诺 API 未提供的更高分辨率；QQ 中转换后的 GIF 继续用作预览。
- Fanbox 受限内容可能需要用户自身的登录权限；这次修改不改变访问权限或代理服务的可用性。
- 搜索、推荐、排行榜、关联作品、用户作品、指定作品、多页、随机推送和订阅共用修复后的发送入口。

## 验证

- 135 项离线测试通过，包含新增 26 项图片链接回归用例。
- 2 项真实数据库测试独立运行通过；原测试套件的 conftest 会模拟 peewee，因此这两项单独执行。
- 全部 Python 文件语法检查通过，JSON 配置解析通过。
- 安装包验证 CRC、单一插件根目录、正斜杠路径及必需文件，排除测试缓存和临时产物。
- 未连接真实 Pixiv 账号、QQ 机器人或部署中的反代服务，不宣称已完成线上联调。

以用户提供的 v1.4.4 ZIP 为基础修复，插件版本元信息保持 v1.4.4。
