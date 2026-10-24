# Reelay 管理控制台（前端）

React 19 + TypeScript（strict）+ Vite + Tailwind CSS v4。基础组件来自 shadcn/ui，放在 `src/components/ui/`，只放用到的组件，样式按本项目调整过。

## 开发

```bash
npm ci
npm run dev -- --port 28641   # /api 代理到 http://127.0.0.1:28080（先启动 python -m reelay.web）
```

开发服务器的 `/api` 默认代理到本机 28080 端口；要连别的后端（如演示数据实例）时设置 `VITE_API_TARGET`：

```bash
VITE_API_TARGET=http://127.0.0.1:28090 npm run dev
```

## 检查、测试与构建

```bash
npm run typecheck
npm run lint:eslint
npm test                      # vitest：请求封装、实时通道、待审批、配置草稿、确认弹窗、格式化与状态映射
npm run build                 # 产物在 dist/，由 python -m reelay.web 直接托管
```

界面规范见 [DESIGN.md](DESIGN.md)（语义色、深色模式、页面结构与移动端约定）。

部署在反向代理的子路径下时，构建前设置 `VITE_BASE_PATH`（如 `/reelay/`），路由与 API 前缀都会随之调整。

## 目录

```
src/
├── api/          # 接口：request.ts 统一请求与错误文案，其余按领域各一个模块（tasks、inbox、config、sources…）
├── components/   # 布局、登录、任务详情等业务组件；app/ 为页面通用组件，ui/ 为 shadcn 组件
├── hooks/        # useAsync（取数与新旧结果）、useDashboard（共用运行状态）、useDirListing（浏览 115 目录）等
├── lib/          # 类型、取值与状态的中文映射（status.ts）、SSE 实时通道、格式化等
├── pages/        # 各页面，按路由拆分；ConfigPage/ 下是「设置」与「插件」两页及共用的配置草稿
├── plugins/      # 内置插件的专用界面（设置编辑器、运行概览里的说明），每个插件一个目录，与后端 reelay/plugins/<名>/ 对应；index.ts 按 config_type 登记
└── __tests__/    # vitest 单元测试
```

总览、待审批、任务、媒体库直接打包；统计（echarts）、设置、插件、追剧订阅、115 目录与运行日志按需加载。
