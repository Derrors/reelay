"""FastAPI 管理服务：管理台 API、生产构建的前端，以及 Agent 用的 ``/mcp``。

开发期前端经 Vite 代理访问 ``/api``；生产构建后若 ``web/dist`` 存在，由同一个服务托管。默认只绑定本机；
暴露到其他设备前必须在「设置 → 运行与安全」设置登录密码（没设密码时只接受本机地址，见 ``auth.Guard``）。

每个资源一个路由模块：``auth``（登录与密钥）、``system``（健康检查、总览、实时推送、日志、投递）、
``tasks``（任务与收件箱）、``config``（配置与插件动作）、``p115``（115 登录与目录浏览）、``sources``（来源）、
``follow``（订阅与不再接收）、``library``（媒体库与统计）、``ai``（AI 服务与洞察）。业务都在
ManagementService；路由只做参数解析，错误由 ``app`` 统一映射成 HTTP 状态码。
"""
from .app import create_app, main

__all__ = ["create_app", "main"]
