# v1.4.9 JM 月排行与总排行

- 新增 `/jm月排行 [页码]`，别名 `/jm月榜`、`/jmmonth`。
- 新增 `/jm总排行 [页码]`，别名 `/jm总榜`、`/jmall`。
- 不填页码默认第 1 页，支持 1–10000 的正整数；错误输入不发起网络请求。
- 两榜均按上游浏览量排序，返回标题、真实漫画 ID 和翻页提示，序号表示本页位置。已知末页时不再显示下一页；越界页不冒充首/末页结果。
- 接入现有 `jm_commands` 大模型工具，可以说“看看 JM 月榜”“给我 JM 总榜第 2 页”，再说“下载第二本的第 1 章”。模型收到实际查询结果中的 ID，无须手动复制。
- 更新文字和图片帮助；沿用原有 JM 开关、权限、代理、域名和重试配置，网络请求在线程池执行。
- 保留章节下载、JM/PICA 查询 ID 回传、Pixiv 断连恢复、依赖补装及 PDF 导出。未新增依赖或必填配置。

使用时上传新版 ZIP 覆盖更新并重载插件。JM 命令需要 `jm_enabled`；自然语言还需要 `jm_llm_enabled` 及可调用工具的 AstrBot 模型。

月榜使用 `month_ranking`，总榜使用 `categories_filter` 的全部时间、全部分类、浏览量排序参数，与 [jmcomic 官方用法](https://github.com/hect0x7/JMComic-Crawler-Python/blob/master/assets/docs/sources/tutorial/0_common_usage.md) 一致。

排行榜来自实时上游数据；离线验证不能确认用户部署环境中的站点连通性或实际榜单内容。直接输入命令的结果是否进入模型上下文取决于 AstrBot 设置，建议通过自然语言查询后继续按序号选书。
