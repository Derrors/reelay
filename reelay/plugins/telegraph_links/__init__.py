"""telegra.ph 页面里的分享链接 —— before_parse Hook。

有的频道不在消息里放 115 分享，而是附一个 telegra.ph 页面（Telegram 的图文发布服务），分享链接写在页面里。
消息本身没有 115 分享或电驴链接时，读取消息直接带的 telegra.ph 页面（不跟进页面里链到的其他页面，
合集页一般只链到各片的页面），把页面里的分享链接或电驴链接补进 ``ctx.raw.urls``。之后的识别、过滤
（``must_have`` 同样检查这些链接）与去重都与链接直接写在消息里时一样。

经 Telegra.ph 的公开接口（``api.telegra.ph/getPage``）读取，走「Telegram」的网络代理设置。
读取失败只写进任务轨迹，不影响其他处理：这条消息会因为没有链接被跳过，可以稍后「重新识别」。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from ...core import proxy
from ...core.contracts import PipelineContext
from ...core.parse_rules import parse_115_url, parse_ed2k
from ...core.registry import register

log = logging.getLogger("reelay.plugin.telegraph_links")

API = "https://api.telegra.ph/getPage/{path}?return_content=true"
MAX_PAGES = 3  # 一条消息最多读几个页面
TIMEOUT = 10
RETRY_DELAY = 2.0
MAX_BYTES = 2_000_000
# telegra.ph 与它的别名 graph.org；/file/... 是页面里的图片，不是页面
_PAGE_RE = re.compile(r"https?://(?:www\.)?(?:telegra\.ph|graph\.org)/([^\s/?#\"'<>]+)", re.I)


def page_paths(text: str, urls: list[str]) -> list[str]:
    """消息里的 telegra.ph 页面路径（已解码），按出现顺序去重。"""
    paths: dict[str, None] = {}
    for content in [*urls, text]:
        for match in _PAGE_RE.finditer(content or ""):
            path = urllib.parse.unquote(match.group(1))
            if path.lower() != "file":
                paths.setdefault(path)
    return list(paths)


def _get_json(url: str) -> dict:
    via = proxy.url_for("telegram")
    if via:
        response = proxy.http_client(via).get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                timeout=TIMEOUT) as resp:
        return json.loads(resp.read(MAX_BYTES).decode("utf-8"))


def _walk(nodes: Any, texts: list[str], hrefs: list[str]) -> None:
    """页面内容是 Node 数组：字符串是文字，对象是 {tag, attrs, children}。"""
    for node in nodes if isinstance(nodes, list) else [nodes]:
        if isinstance(node, str):
            texts.append(node)
        elif isinstance(node, dict):
            attrs = node.get("attrs") or {}
            if isinstance(attrs.get("href"), str):
                hrefs.append(attrs["href"])
            _walk(node.get("children") or [], texts, hrefs)
            if node.get("tag") in ("p", "br", "li", "h3", "h4", "pre", "blockquote", "figure"):
                texts.append("\n")


def page_links(content: Any) -> list[str]:
    """页面里的分享链接：第一个 115 分享（访问码写在页面文字里时带到链接上），以及电驴链接。"""
    texts: list[str] = []
    hrefs: list[str] = []
    _walk(content, texts, hrefs)
    text = "".join(texts)
    links = []
    share_url, code, password = parse_115_url(text, hrefs)
    if code:
        if password and not re.search(r"[?&]password=", share_url or ""):
            share_url = f"https://115.com/s/{code}?password={password}"
        links.append(share_url)
    links += [item["url"] for item in parse_ed2k(text, hrefs)]
    return links


def fetch_links(path: str) -> list[str]:
    """读取一个页面，返回其中的分享链接；页面不存在或接口出错时抛出异常。"""
    data = _get_json(API.format(path=urllib.parse.quote(path, safe="")))
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "接口没有返回页面"))
    return page_links((data.get("result") or {}).get("content") or [])


@register(kind="hook", stage="before_parse", order=50)
class TelegraphLinks:
    name = "telegraph_links"
    ui_label = "telegra.ph 页面链接"
    ui_description = ("有的频道把 115 分享放在消息附带的 telegra.ph 页面里：消息本身没有分享或电驴链接时，"
                      "读取这些页面取出链接。经「网络代理」中 Telegram 的设置访问。")
    stage = "before_parse"
    order = 50

    async def run(self, ctx: PipelineContext) -> bool:
        raw = ctx.raw
        if parse_115_url(raw.text, raw.urls)[1] or parse_ed2k(raw.text, raw.urls):
            return True  # 消息里已经有链接，不读页面（重新识别时也是这样）
        paths = page_paths(raw.text, raw.urls)[:MAX_PAGES]
        if not paths:
            return True
        found: list[str] = []
        errors: list[str] = []
        for path in paths:
            for attempt in range(2):
                try:
                    found += await asyncio.to_thread(fetch_links, path)
                    break
                except Exception as exc:  # noqa: BLE001 - 读不到页面只影响这条消息
                    if attempt:
                        errors.append(f"{path}（{type(exc).__name__}：{str(exc)[:80]}）")
                    else:
                        await asyncio.sleep(RETRY_DELAY)
        new = [url for url in dict.fromkeys(found) if url not in raw.urls]
        raw.urls.extend(new)
        ctx.ns(self.name).update(pages=paths, links=len(new), errors=errors)
        if new:
            ctx.log(f"从 telegra.ph 页面取到 {len(new)} 条链接")
        elif errors:
            log.warning("读取 telegra.ph 页面失败：%s", "；".join(errors))
            ctx.log(f"读取 telegra.ph 页面失败，可稍后重新识别：{'；'.join(errors)}")
        else:
            ctx.log("telegra.ph 页面里没有 115 分享或电驴链接")
        return True
