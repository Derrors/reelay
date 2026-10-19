# 管理台设计规范

青绿品牌色 + 冷灰中性色，浅色/深色两套主题（默认跟随系统）。新页面与改动请遵循本规范，保证整体一致。

## 页面结构

- 每个页面用 `components/app/Page`（标题、一句话说明、右上角操作），内容区最大宽度 1280px，边距与移动端底部留白已内置。
- 内容按块用 `components/app/Panel` 分组：标题 15px 半粗，可带图标与右侧操作；块之间 `gap-5`。
- 指标用 `StatCard`，空状态用 `EmptyState`，加载中用 `ui/skeleton`，详情与编辑用右侧抽屉 `Sheet`（不要用居中弹窗承载长表单）。

## 颜色

- 只使用语义变量：`bg-background`/`bg-card`/`bg-muted`、`text-foreground`/`text-muted-foreground`、`border-border`、`primary`/`success`/`warning`/`destructive`/`info`。
- 状态色统一走 `lib/status.ts` 的 `Tone` 与 `TONE_CLASS`，任务状态用 `StatusBadge`。
- 不写死 `bg-white`、`text-gray-*`、十六进制颜色（品牌图标渐变除外），否则深色模式会出错。改完请在深色模式下检查一遍。

## 文字与间距

- 页面标题 22–24px 半粗；块标题 15px 半粗；正文 14px；辅助信息 12px `text-muted-foreground`。
- 数字使用 `tabular-nums`；圆角卡片 `rounded-xl`，控件 `rounded-md`/`rounded-lg`；卡片内边距 `p-5`（移动端 `p-4`）。
- 图标统一 lucide，常规尺寸 `size-4`。

## 表单

- 标签在上（13px 中粗），说明在下（12px 弱化）；下拉框用 `app/Select`，开关用 `ui/switch`。
- 界面上只出现中文标签，不直接展示内部取值（如 `default`、`p115`、`json_schema`），映射放在 `lib/status.ts`。
- 有未保存修改时给出明确提示与保存入口；危险操作（删除、忽略）使用 `destructive` 并在必要时二次确认。

## 媒体内容

- 海报用 `MediaCover`（TMDB 海报，缺失时自动生成文字封面），媒体信息行用 `MediaMeta`。
- 时间默认显示相对时间（`lib/format.ts` 的 `relativeTime`），悬停 `title` 显示完整时间。

## 响应式

- 移动端优先：表格在 `md` 以下改为堆叠卡片，避免横向滚动；可点击区域高度不小于 36px。
- 底部标签栏覆盖在移动端页面底部，`Page` 已预留空间。
