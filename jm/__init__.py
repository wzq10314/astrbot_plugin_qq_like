"""
JMComic - 禁漫天堂（JM）适配层

由上游独立插件 jmhelper 改编而来，作为 qq_like 的 vendored 子包使用。与上游的差异：
- 不再是 AstrBot Star：由宿主插件持有实例并调用其方法
- 无 @register / @filter 装饰器：命令解析与分发交由宿主完成
- 配置键统一加 ``jm_`` 前缀，避免与宿主配置冲突
- 数据目录由宿主传入
- 下载结果通过 reader 服务生成网页阅读链接，而非上传群文件

搜索、查看、下载禁漫天堂（JM）漫画，支持搜索结果图片化展示。
- 支持整本、单章和指定章节下载，通过 reader 服务生成网页阅读链接
- 无需登录即可下载大部分内容
"""

from .plugin import JmHelper

__all__ = ["JmHelper"]
