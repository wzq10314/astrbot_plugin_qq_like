"""
JMComic 消息格式化工具
"""


class JmFormatter:
    """JM 消息格式化器"""

    @staticmethod
    def format_album(album) -> str:
        """格式化单本漫画信息"""
        lines = [
            f"📚 {album.name} [{album.album_id}]",
            "━━━━━━━━━━━━━━━━━━━",
            f"👤 作者: {'、'.join(album.authors) if album.authors else '未知'}",
            f"📅 发布: {album.pub_date}",
            f"🔄 更新: {album.update_date}",
            f"📊 统计: {album.views}浏览 | {album.likes}喜欢 | {album.comment_count}评论",
            f"📖 页数: {album.page_count}页 (共{len(album.episode_list)}章)",
        ]
        if album.tags:
            tags = album.tags[:8]
            tags_str = "、".join(tags)
            if len(album.tags) > 8:
                tags_str += f"...等{len(album.tags)}个标签"
            lines.append(f"🏷️ 标签: {tags_str}")
        if album.works:
            lines.append(f"🔗 系列: {'、'.join(album.works)}")
        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 /jm章节 <ID> 查看目录；/jm下载 <ID> [章节] 下载整本或指定章节")
        return "\n".join(lines)

    @staticmethod
    def format_chapters(album, start: int = 0, limit: int = 50) -> str:
        """Use displayed 1-based positions, including albums with sparse sort IDs."""
        stop = min(start + limit, len(album))
        lines = [f"📚 {album.name} [{album.album_id}]", f"📑 章节目录 {start + 1}-{stop} / 共{len(album)}章"]
        for index in range(start, stop):
            photo = album[index]
            name = " ".join(str(photo.name).split())[:100]
            lines.append(f"{index + 1}. {name}")
        lines.append(f"💡 /jm下载 {album.album_id} 1-3,7（按上方目录序号选择）")
        return "\n".join(lines)

    @staticmethod
    def format_search_results(albums, keyword: str, page: int = 1) -> str:
        """格式化搜索结果"""
        if not albums:
            return f'🔍 未找到与 "{keyword}" 相关的结果'
        lines = [f"🔍 搜索: {keyword} (第{page}页)", "━━━━━━━━━━━━━━━━━━━"]
        for i, album in enumerate(albums, 1):
            title = album.name
            if len(title) > 40:
                title = title[:40] + "..."
            lines.append(f"{i}. {title}\n   ID: {album.album_id}")
        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📄 下一页: /jm搜索 {keyword} {page + 1}")
        lines.append("💡 回复 /jm详情 <ID> 查看详情")
        return "\n".join(lines)

    @staticmethod
    def _ranking_parts(albums, period: str, page: int = 1, page_count=None):
        """标题、完整漫画条目和翻页提示分开构建，分段不拆开标题与 ID。"""
        title = {"month": "月排行", "all": "总排行"}[period]
        command = f"/jm{title}"
        known_pages = type(page_count) is int and page_count >= 0
        pages = f" / 共{page_count}页" if known_pages else ""
        header = [f"🏆 JM {title} · 第{page}页{pages}", "按浏览量排序 · 以下为本页序号", "━━━━━━━━━━━━━━━━━━━"]
        entries = []
        if not albums:
            entries.append("📭 本页暂无排行结果")
        else:
            for index, (album_id, album_name) in enumerate(albums, 1):
                name = " ".join(str(album_name).split())
                if len(name) > 40:
                    name = name[:40] + "..."
                entries.append(f"{index}. {name}\n   ID: {album_id}")
        footer = ["━━━━━━━━━━━━━━━━━━━"]
        if page > 1:
            previous = min(page - 1, max(1, page_count)) if known_pages else page - 1
            footer.append(f"📄 上一页: {command} {previous}")
        if page < 10000 and (page < page_count if known_pages else bool(albums)):
            label = "下一页" if known_pages else "尝试下一页"
            footer.append(f"📄 {label}: {command} {page + 1}")
        footer.append("💡 /jm详情 <ID> 查看详情；/jm下载 <ID> [章节] 下载")
        return header, entries, footer

    @staticmethod
    def format_ranking(albums, period: str, page: int = 1, page_count=None) -> str:
        """保留上游顺序及真实 ID，序号表示当前页内的位置。"""
        header, entries, footer = JmFormatter._ranking_parts(albums, period, page, page_count)
        return "\n".join(header + entries + footer)

    @staticmethod
    def format_ranking_messages(albums, period: str, page: int = 1, page_count=None) -> list[str]:
        """每份最多 50 条漫画；完整保留本页序号、标题和 ID。"""
        header, entries, footer = JmFormatter._ranking_parts(albums, period, page, page_count)
        header_text, footer_text = "\n".join(header), "\n".join(footer)
        groups = [entries[start:start + 50] for start in range(0, len(entries), 50)]
        messages = []
        for number, group in enumerate(groups, 1):
            label = f"（第{number}/{len(groups)}份 · 本份{len(group)}条）\n" if albums else ""
            text = label + header_text + "\n" + "\n".join(group)
            if number == len(groups):
                text += "\n" + footer_text
            messages.append(text)
        return messages

    @staticmethod
    def help_text() -> str:
        """帮助文本"""
        return (
            "📖 JM漫画插件 (JMComic)\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "/jm搜索 <关键词> [页码] - 搜索漫画\n"
            "/jm月排行 [页码] - 月榜（按浏览量）\n"
            "/jm总排行 [页码] - 总榜（按浏览量）\n"
            "别名: /jm月榜、/jm总榜；不填页码默认第1页\n"
            "/jm详情 <ID> - 查看漫画详情\n"
            "/jm章节 <ID> - 查看章节目录\n"
            "/jm下载 <ID> - 整本下载\n"
            "/jm下载 <ID> 1 - 单章下载\n"
            "/jm下载 <ID> 1-5 - 连续章节\n"
            "/jm下载 <ID> 1,3,7 - 指定章节\n"
            "/jm下载 <ID> 1-3,7 - 组合选择\n"
            "/jm 或 /jm帮助 - 图片帮助\n"
            "/jm帮助 文字 - 完整文字帮助\n"
            "/jm清理 [天数] - 清理缓存\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💡 参数之间留空格；章节按 /jm章节 目录序号选择。\n"
            "下载完成后发送网页阅读链接；未配置阅读服务时发送文件。"
        )
