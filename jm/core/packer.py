"""
JMComic 打包模块

JM 主要使用 reader 服务生成网页阅读链接，不需要传统的 ZIP/PDF 打包。
此模块保留用于未来扩展（如需要发送文件时）。
"""

from pathlib import Path


class JmPacker:
    """JM 打包器（目前为空实现，JM 使用 reader 服务）"""

    def __init__(self):
        pass

    def pack(self, source_dir: Path, output_name: str, output_dir: Path = None):
        """
        打包（当前为空实现）

        JM 主要通过 reader 服务生成网页阅读链接，不需要传统的打包操作。
        """
        # JM 使用 reader 服务，不需要打包
        return None
