# Reelay 管理控制台（前端）

React 19 + TypeScript + Vite + Tailwind CSS v4，组件基于 shadcn/ui（只保留实际用到的组件，位于 `src/components/ui/`）。

## 开发

```bash
npm ci
npm run dev -- --port 28641   # /api 代理到 http://127.0.0.1:28080（先启动 python -m reelay.web）
```

## 检查与构建

```bash
npm run typecheck
npm run lint:eslint
npm run build                 # 产物在 dist/，由 python -m reelay.web 直接托管
```

界面规范见 [DESIGN.md](DESIGN.md)（语义色、深色模式、页面结构与移动端约定）。

部署在反向代理的子路径下时，构建前设置 `VITE_BASE_PATH`（如 `/reelay/`），路由与 API 前缀都会随之调整。

## 目录

```
src/
├── api/          # REST 客户端（apiUrl 统一拼接前缀）
├── components/   # 布局、登录、业务组件；app/ 为页面通用组件，ui/ 为 shadcn 组件
├── lib/          # 类型、状态文案、SSE 实时通道
├── pages/        # 各页面，按路由拆分；统计页按需加载
└── plugins/      # 内置插件的专用界面（设置编辑器、插件页面），每个插件一个目录（与后端 reelay/plugins/<名>/ 对应），index.ts 按 config_type 登记编辑器
```
