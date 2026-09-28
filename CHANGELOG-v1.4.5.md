# v1.4.5 安装依赖修复

- 修复 JM 初始化启用 img2pdf，但 requirements.txt 未声明 img2pdf 导致插件上传安装失败的问题。
- 显式补齐 img2pdf、PyYAML、requests、pydantic。保留原有依赖，由包管理器安装它们的传递依赖（包括 img2pdf 的 pikepdf）。
- JM 依赖最低版本调整为 2.7.7，启用 plugins.dependencies_strategy: auto-install，在重载时缺少 JM 可选依赖的情况下自动补装。保留 PDF 输出，不忽略依赖错误。
- APScheduler 限制为 3.x，匹配当前代码使用的 AsyncIOScheduler API。
- 不修改 QQ、Pixiv、哔咔及 JM 的命令和配置。

## 安装

在 AstrBot 插件管理中上传本 ZIP。requirements.txt 与 main.py、metadata.yaml 位于同一个插件目录，AstrBot 负责自动安装声明的 Python 依赖，无须用户逐个执行 pip 命令。安装需要服务器可访问 Python 包源并有写入运行环境的权限；真实的下载/权限错误仍会报出。

Playwright 的浏览器可执行文件和在线阅读服务不是 Python 包，不能由 requirements.txt 安装；沿用原插件的配置和渲染回退机制。此修复不会自动部署阅读服务或修改系统软件。

依据：
- https://docs.astrbot.app/dev/star/plugin-new.html
- https://github.com/hect0x7/JMComic-Crawler-Python/blob/master/src/jmcomic/jm_plugin.py
