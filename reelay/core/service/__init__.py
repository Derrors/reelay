"""统一应用服务层：Web API、Telegram 控制 Bot 与 MCP 共用这里的规则。

服务只改变任务/配置/用户数据的权威状态，不直接绕过 Pipeline 操作 115。这样多个入口并发操作时，
状态检查与错误语义一致。按领域分成几个模块，由 ``ManagementService`` 组合：

- ``overview``：运行概况、连接状态、任务列表与详情；
- ``tasks``：批准、忽略、重试、重新识别、重新整理、核验转存、修正识别；
- ``review``：收件箱的版本择优、审批建议与自动审批建议；
- ``sources``：识别测试、新来源接入、来源识别规则与来源质量；
- ``catalog``：媒体库与缺集；
- ``follow``：追剧订阅与不再接收；
- ``ai_service``：AI 服务设置与一句话生成规则；
- ``settings``：配置与密钥。
"""
from __future__ import annotations

from .ai_service import AIServiceMixin
from .base import ConflictError, NotFoundError, parsed_sample
from .catalog import CatalogMixin
from .follow import FollowMixin
from .overview import OverviewMixin
from .review import ReviewMixin
from .settings import SettingsMixin
from .sources import SourceMixin
from .tasks import TaskOpsMixin

__all__ = ["ManagementService", "ConflictError", "NotFoundError", "parsed_sample"]


class ManagementService(OverviewMixin, TaskOpsMixin, ReviewMixin, SourceMixin, CatalogMixin, FollowMixin,
                        AIServiceMixin, SettingsMixin):
    """Web、控制 Bot 与 MCP 使用的全部业务操作。"""
