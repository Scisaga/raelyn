from __future__ import annotations

from yt_dlp.utils import std_headers


# 跟随当前安装的 yt-dlp 更新浏览器版本范围，避免长期固化为过期 Chrome UA。
BROWSER_USER_AGENT = str(std_headers["User-Agent"])
