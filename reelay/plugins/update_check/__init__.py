"""版本更新检查 —— on_daily 事件监听。

开关同时管管理台侧栏的新版本提示（见 ``core/updates.py``）与源码部署的一键升级（``core/upgrade.py``）：
关掉后不再联网查询。每天随日报的时间检查一次，发现新版本时由控制 Bot 提醒一次（``notify`` 可关），
同一个版本不重复提醒。这里只提醒，不升级。
"""
from __future__ import annotations

import asyncio

from ...core import runtime, updates
from ...core.contracts import PipelineContext
from ...core.messages import esc
from ...core.registry import register

NOTE_LINES = 6


def render(result: dict) -> str:
    """提醒正文（Telegram HTML）：版本、更新说明里的前几条、升级方法。"""
    items = [line.strip()[2:].strip() for line in result["notes"].splitlines() if line.strip().startswith(("- ", "* "))]
    blocks = [f"🆕 <b>Reelay {esc(result['latest'])} 已发布</b>\n当前版本 {esc(result['current'])}"]
    if items:
        lines = [f"• {esc(item)}" for item in items[:NOTE_LINES]]
        if len(items) > NOTE_LINES:
            lines.append(f"… 共 {len(items)} 项")
        blocks.append("\n".join(lines))
    if not result["docker"]:
        how = "在管理台点「立即升级」，或拉取最新代码后重新安装并重启"
    elif result["pinned"]:
        how = (f"先把 .env 里的 REELAY_VERSION 改成 {esc(result['latest'])}，"
               "再执行 <code>docker compose pull &amp;&amp; docker compose up -d</code>")
    else:
        how = "执行 <code>docker compose pull &amp;&amp; docker compose up -d</code>"
    blocks.append(f"升级：{how}；升级前建议先备份数据卷。\n<a href=\"{esc(result['url'])}\">查看更新说明</a>")
    return "\n\n".join(blocks)


@register(kind="event", event="on_daily")
class UpdateCheck:
    name = updates.NAME
    ui_label = "版本更新检查"
    ui_description = ("定期查询 GitHub 上的最新正式版：管理台侧栏提示新版本，控制 Bot 每个新版本提醒一次。"
                      "源码部署可以在管理台确认后一键升级，Docker 部署需自己拉取新镜像；不会自动升级。"
                      "连不上 GitHub 时可以在「设置 → 网络代理」让 GitHub 走代理。")
    ui_fields = [
        {'path': 'notify', 'label': '控制 Bot 推送新版本提醒', 'type': 'boolean', 'default': True},
    ]
    event = "on_daily"

    async def handle(self, ctx: PipelineContext) -> None:
        services = runtime.services()
        result = await asyncio.to_thread(updates.check, services.store)
        if runtime.plugin_config(self.name).get("notify", True) and updates.take_notice(services.store, result):
            services.notify(render(result), key=self.name, html=True)
