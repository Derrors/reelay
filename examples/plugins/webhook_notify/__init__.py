"""示例插件：Webhook 通知渠道（A 类 Provider）。

把任务事件（identified 识别完成、pending_approval 待确认、saved 已入库、organize_failed 整理失败、
failed 失败、transfer_uncertain 转存结果待核对）和系统通知（告警、审批汇总、日报）以 JSON POST
到 ``plugins.webhook.url``，可接 Bark、Gotify、ntfy、Home Assistant 或自建中转服务。

启用：
1. 把 ``webhook_notify`` 目录放进 ``data/plugins/``（Docker 部署放进 ``deploy/plugins/``，它挂载到
   ``/app/data/plugins``），重启主进程与管理台；
2. 在管理台「插件」页开启「Webhook 通知」并填写地址（即 ``providers.notify`` 加上 ``webhook``，
   地址写在 ``plugins.webhook.url``）。

发送失败直接抛异常即可：内核的 Outbox 会按退避策略重试，不影响转存主流程。
载荷刻意不含分享链接与访问码。
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

from reelay.core import runtime
from reelay.core.contracts import PipelineContext
from reelay.core.registry import register


@register(kind="provider", ptype="notify")
class WebhookNotify:
    name = "webhook"
    ui_label = "Webhook 通知"
    ui_description = "把任务事件与系统通知以 JSON POST 到指定地址。"
    ui_fields = [{"path": "url", "label": "Webhook 地址", "type": "string"}]  # 相对 plugins.webhook

    async def notify(self, ctx: PipelineContext, event: str) -> None:
        p = ctx.parsed
        await self._post({
            "event": event,
            "task_id": ctx.task_id,
            "state": ctx.state.value,
            "source": ctx.raw.source,
            "title": p.title if p else None,
            "year": p.year if p else None,
            "episodes": p.episodes if p else [],
            "save_dir": ctx.save_dir_alias,
        })

    async def notice(self, text: str) -> None:
        await self._post({"event": "notice", "text": text})

    @staticmethod
    async def _post(payload: dict) -> None:
        url = runtime.plugin_config("webhook").get("url")
        if not url:
            return
        request = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        await asyncio.to_thread(lambda: urllib.request.urlopen(request, timeout=10).close())
