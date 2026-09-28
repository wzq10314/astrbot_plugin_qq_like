# QQ 点赞与状态助手 · AstrBot

[![版本](https://img.shields.io/github/v/release/wzq10314/astrbot_plugin_qq_like)](https://github.com/wzq10314/astrbot_plugin_qq_like/releases)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-4.28.1+-purple)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue)](LICENSE)
![Views](https://komarev.com/ghpvc/?username=wzq10314-astrbot-plugin-qq-like&label=Views)

QQ 名片点赞、服务器状态图、Pica 漫画与 Pixiv 插画集成插件。支持 OneBot11 / NapCat，提供易记中文命令、简繁搜索回退，以及可选的漫画阅读页和 Pixiv 原图页面。

**这是 AstrBot 整合适配版，不是各上游作者的官方发布。感谢原作者开放源码。**

## 快速开始

1. 在 AstrBot 插件管理中通过仓库地址安装：`https://github.com/wzq10314/astrbot_plugin_qq_like`，或上传 [Release 安装包](https://github.com/wzq10314/astrbot_plugin_qq_like/releases/latest)。
2. AstrBot 会按 `requirements.txt` 安装 Python 依赖。安装失败时需检查服务器到软件源的网络；代理、登录凭据和浏览器系统依赖需要自行配置。
3. 保存配置并重载插件。先试 `#状态`、`#赞我`；查看图片功能用 `/pica帮助`、`/pixiv帮助`。

状态图优先使用可用 Chromium；浏览器不可用时使用基础图片回退。在 AstrBot 同一环境可执行 `python -m playwright install chromium` 安装浏览器，Linux 缺少系统库时需管理员另行安装。

### v1.6.0 的主要变化

- PICA 和 Pixiv 中文命令接入自然语言工具，旧英文别名继续兼容；权限与原命令一致，登录凭据仅由用户在私聊中提交。
- PICA 支持多章节下载：`1-5`、`1,3,7`、`1-3,7`；后台下载、去重排序、独立目录打包，成功章节合成一个阅读链接，失败章节单独提示。
- 恢复单章、整本的阅读链接优先回复，以及 PICA / Pixiv 原词无结果时的简繁兼容搜索。
- Pixiv 群聊转发遇到 1200 或发送超时时，引用原消息告知并尝试私聊原请求者；原图网页通知跟随私聊。
- 搜图等待提示、原图网页准备提示；LLM 搜图数量遵循后台 `return_count`，不再固定上限五张。
- PICA / Pixiv 图片菜单只发图片；后台“图片菜单机器人名称”可自定义标题，保留字体及人物比例。
- 阅读页保留漫画连续无缝阅读；Pixiv 图片与简介、查看和下载按钮放在同一卡片。

### 阅读网页是可选服务

普通 QQ 回复不依赖网页服务。需要在线阅读或原图下载时，请按 [阅读服务部署说明](docs/reader.md) 自行部署随仓库提供的 `reader/`。仓库不提供共享域名或免费托管服务，也不会自动开放公网端口。

更新已有阅读服务时，需要在 `reader-client.json` 中配置自己的 `public_base`。插件标识及数据目录仍为 `astrbot_plugin_qq_like`，无需重新绑定账号。

## 目录

- [使用前先看](#使用前先看)
- [点赞](#点赞)
- [服务器状态](#服务器状态)
- [哔咔漫画 pica](#哔咔漫画-pica)
- [PIXIV pixiv_reborn](#pixiv-pixiv_reborn)
- [自然语言调用](#自然语言调用)
- [安装与配置](#安装与配置)
- [致谢与许可](#致谢与许可)

## 使用前先看

- 点赞与状态命令使用 `#` 前缀；pica 与 pixiv 命令使用 `/` 前缀（`/pica搜索`、`/pixiv`）。
- `关键词`、`QQ号`、`PID`、`UID`、`作品ID` 是需要替换的参数。
- `@用户` 是 QQ 的真实艾特，不是手动输入昵称。
- 表中"管理员"指 **AstrBot 管理员**，不等于 QQ 群管理员。
- **哔咔为成人向平台**，`pica_enabled` 默认关闭，需管理员在后台手动开启。
- **PIXIV 默认过滤 R18 内容**，`r18_mode` 默认为"过滤 R18"。

## 点赞

### 全部命令

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `#赞我` | 给当前发消息的人点赞 | `#赞我` |
| `#赞我 次数` | 给自己点赞，指定次数 | `#赞我 20` |
| `#点赞 QQ号` | 给指定 QQ 点赞 | `#点赞 123456789` |
| `#点赞 QQ号 次数` | 给指定 QQ 点赞并指定次数 | `#点赞 123456789 50` |
| `#赞他 @用户` | 给被艾特的人点赞 | `#赞他 @用户` |
| `#赞他 @用户 次数` | 给被艾特的人点赞并指定次数 | `#赞他 @用户 20` |
| `#点赞帮助` | 查看用法 | `#点赞帮助` |

次数范围 **1～50**，默认 50。冷却或接口拒绝后停止。

## 服务器状态

| 命令 | 展示内容 | 权限 |
| --- | --- | --- |
| `#状态` | CPU、内存、磁盘、网络等基础状态图 | 普通用户 |
| `#状态pro` | 增加负载、交换空间、线程及容器信息 | 普通用户 |
| `#状态debug` | 基础状态及诊断信息 | 管理员 |
| `#状态prodebug` | 完整状态及诊断信息 | 管理员 |
| `#扩展帮助` | 状态帮助 | 普通用户 |

## 哔咔漫画 pica

> **成人向平台**，需管理员在后台配置 `pica_enabled: true` 后使用。
> 直连 pica 官方 API（HMAC-SHA256 签名），不再依赖第三方 HibiAPI 镜像。
> 账号按 QQ 号隔离，每个用户可绑定自己的哔咔账号。

### 命令

| 命令 | 用途 |
| --- | --- |
| `/pica` 或 `/pica帮助` | 查看帮助 |
| `/pica登录 <邮箱> <密码>` | 绑定当前 QQ 的哔咔账号 |
| `/pica退出` | 解绑当前 QQ 的哔咔账号 |
| `/pica状态` | 查看当前账号状态 |
| `/pica搜索 <关键词> [页码]` | 搜索漫画 |
| `/pica详情 <ID>` | 查看漫画详情 |
| `/pica章节 <ID>` | 查看章节列表 |
| `/pica下载 <ID> <章节号>` | 下载单章节 |
| `/pica下载 <ID> 1-5` | 下载第 1～5 章 |
| `/pica下载 <ID> 1,3,7` | 只下载第 1、3、7 章 |
| `/pica下载 <ID> 1-3,7` | 下载第 1～3 章和第 7 章 |
| `/pica下载 <ID>` | 整本下载（后台任务） |
| `/pica排行 [H24\|D7\|D30]` | 排行榜（24小时/7天/30天） |
| `/pica分类 <分区名> [页码]` | 分区浏览 |
| `/pica分区` | 查看分区列表 |
| `/pica收藏 <ID>` | 收藏/取消收藏 |
| `/pica我的收藏 [页码]` | 我的收藏 |
| `/pica签到` | 每日签到 |
| `/pica清理 [天数]` | 清理缓存 |

### 配置

| 配置 | 说明 |
| --- | --- |
| `pica_enabled` | 哔咔功能总开关（默认关闭） |
| `pica_account` / `pica_password` | 默认账号（用户可用 /pica登录 绑定自己的） |
| `allow_default_account` | 是否允许未绑定用户使用默认账号 |
| `pica_use_proxy` / `pica_proxy_url` | API 代理 |
| `pack_format` | 下载打包格式（zip/pdf/long_img/images/none） |
| `pack_password` | 打包密码 |
| `pica_admin_only` / `pica_admin_ids` | 仅管理员模式 |

## PIXIV pixiv_reborn

> 直连 pixiv 官方 API（pixivpy3），需配置 `refresh_token` 才能使用。
> 默认过滤 R18 内容；AI 作品显示方式可在后台设置。

### 插画搜索

| 命令 | 用途 |
| --- | --- |
| `/pixiv <标签>` | 标签搜索 |
| `/pixivpid <PID>` | 按 PID 查作品 |
| `/pixiv排行 [模式] [日期]` | 排行榜 |
| `/pixiv推荐` | 推荐作品 |
| `/pixiv组合 <标签>` | AND 逻辑搜索 |
| `/pixiv深搜 <标签>` | 深度搜索（多页） |
| `/pixiv相关 <PID>` | 相关作品 |
| `/pixiv热门 <标签> [期间] [页数]` | 按热度搜索 |
| `/pixiv最新` | 最新插画 |
| `/pixiv热词` | 热门标签 |

### 用户

| 命令 | 用途 |
| --- | --- |
| `/pixiv搜画师 <用户名>` | 搜索用户 |
| `/pixiv画师 <UID>` | 用户详情 |
| `/pixiv作品 <UID>` | 用户作品 |

### 小说

| 命令 | 用途 |
| --- | --- |
| `/pixiv小说 <标签>` | 搜索小说 |
| `/pixiv小说推荐` | 推荐小说 |
| `/pixiv最新小说` | 最新小说 |
| `/pixiv小说系列 <ID>` | 小说系列 |
| `/pixiv小说下载 <ID>` | 下载小说为 PDF |

### 订阅

| 命令 | 用途 |
| --- | --- |
| `/pixiv订阅 <画师ID>` | 订阅画师 |
| `/pixiv退订 <画师ID>` | 取消订阅 |
| `/pixiv订阅列表` | 查看订阅列表 |

### 随机搜索

| 命令 | 用途 |
| --- | --- |
| `/pixiv添加标签 <标签>` | 添加随机搜索标签 |
| `/pixiv删除标签 <序号>` | 删除标签 |
| `/pixiv标签列表` | 查看标签列表 |
| `/pixiv暂停推送` | 暂停随机搜索 |
| `/pixiv恢复推送` | 恢复随机搜索 |
| `/pixiv推送状态` | 查看队列状态 |
| `/pixiv立即推送` | 强制执行（调试用） |

### Fanbox

| 命令 | 用途 |
| --- | --- |
| `/pixiv赞助作者 <创作者> [数量]` | 创作者信息和帖子 |
| `/pixiv赞助帖子 <帖子ID>` | 帖子详情 |
| `/pixiv赞助推荐 [数量]` | 推荐创作者 |
| `/pixiv赞助下载 <创作者ID>` | 批量下载帖子 |
| `/pixiv下载进度` | 下载进度 |
| `/pixiv停止下载` | 停止下载 |
| `/pixiv已下载` | 查看已下载内容 |

### 其他

| 命令 | 用途 |
| --- | --- |
| `/pixiv帮助` | 查看帮助 |
| `/pixiv设置 [参数] [值]` | 查看或动态设置配置 |
| `/pixivAI设置 [值]` | AI 作品显示设置 |

### 配置

| 配置 | 说明 |
| --- | --- |
| `refresh_token` | PIXIV refresh_token（必填） |
| `r18_mode` | R18 过滤模式（默认"过滤 R18"） |
| `return_count` | 每次返回图片数量 |
| `proxy` | API 代理地址 |
| `image_proxy_host` | 图片反代地址 |
| `subscription_enabled` | 订阅功能开关 |
| `fanbox_sessid` | Fanbox 会话 Cookie |

## 自然语言调用

模型需要支持工具调用，且当前人格允许使用相应工具。命令与工具沿用当前发起用户的身份和功能权限。

| 工具名 | 后台开关 / 用途 |
| --- | --- |
| `qq_profile_like` | `llm_enabled`；例如“给我点50个赞” |
| `server_status_image` | 服务器状态图 |
| `pica_commands` | `pica_llm_enabled`；PICA 中文命令及旧别名，例如“搜索星空旅行”“下载这本第1到5章” |
| `pixiv_commands` | `pixiv_commands_llm_enabled`；Pixiv 中文命令及旧别名，例如“查询这个PID”“查看我的订阅” |
| `pixiv_search_illust` | 插画搜索；用户未指定数量时使用后台 `return_count`，指定数量时最多请求该上限 |
| `pixiv_search_novel` | 小说搜索和下载 |

下载、收藏、订阅及修改设置仅在用户明确要求时执行；管理员设置仍检查管理员权限。缺少作品ID等必要信息时，模型应先查询或询问用户，不可猜测。工具结束不代表后台下载已完成，下载完成后会另外通知。

多章节参数为文字：`{"comic_id":"作品ID","ep":"1-3,7"}`；旧命令 `/picadl` 同样支持。一次最多选择1000章，未填章节参数为整本下载。中文逗号也可用，顺序按章号排列，重复章节只下载一次。

### 等待与超时

AstrBot 主配置中的 **工具调用超时时间（秒）** 对应 `agent_runner.config.misc.tool_call_timeout`。单章自然语言下载超过默认120秒时，可自行改成600秒后保存生效；插件更新不会修改他人的全局配置。多章节和整本下载使用后台任务。

Pixiv 图片准备上限45秒，与QQ发送等待分开计时；群聊转发等待60秒、私聊90秒。原图网页在图片回复流程结束后生成，下载原尺寸图片可能比QQ预览慢，期间会发送等待提示。不要因链接稍晚出现而重复下载。

### 图片菜单

`/pica帮助`、`/pixiv帮助` 发送图片菜单；可在后台 **图片菜单机器人名称** (`menu_bot_name`) 填入自己的机器人名，留空时尝试读取机器人昵称。自定义标题使用随包提供的霞鹜文楷，需可用 Chromium 渲染；字体许可见 `assets/fonts/`。

## 安装与配置

1. AstrBot 后台上传插件 ZIP，安装或覆盖更新。
2. 在插件配置中保存需要的功能开关及接口配置，重载插件。
3. 配置 `refresh_token` 启用 PIXIV，配置 `pica_enabled: true` 启用哔咔。

Python 依赖见 `requirements.txt`。美化状态图另需可用浏览器。

## 致谢与许可

- 点赞与状态图：感谢 [yeyang52/yenai-plugin](https://github.com/yeyang52/yenai-plugin)
- 哔咔漫画：整合自 [huashuiyue07/astrbot_plugin_pica](https://github.com/huashuiyue07/astrbot_plugin_pica) v1.4.1
- PIXIV：整合自 [vmoranv/astrbot_plugin_pixiv_reborn](https://github.com/vmoranv-reborn/astrbot_plugin_pixiv_reborn) v1.7.5

整合发布采用 AGPL-3.0；上游各部分保留各自版权及许可。具体来源见 [NOTICE](NOTICE.md) 和 [licenses](licenses/)。

## 中文简繁搜索兼容

`/pica搜索` 与 `/pixiv`（含 `/pixiv搜索`）先查原始关键词；上游无结果时自动尝试简体、繁体写法，最多 3 个不同关键词。有结果即停止，不合并结果、不改动过滤设置；网络或认证报错不会触发转换重试。

漫画翻页沿用实际命中的词；已有总数但当前页为空时不会切换搜索词。含日文假名的词保持原样，纯汉字可能属于中文或日文，因此始终先查原词。转换不是翻译，也不包含作品译名映射；只有日文标签的作品仍可能需要日文标签。使用 OpenCC 词库做本地转换，依赖随 requirements 安装。
