# 管理台设计规范

青绿品牌色 + 冷灰中性色，浅色/深色两套主题（默认跟随系统）。新页面与改动请遵循本规范，保证整体一致。

## 页面结构

- 每个页面用 `components/app/Page`（标题、一句话说明、右上角操作），内容区最大宽度 1280px，边距与移动端底部留白已内置。
- 内容按块用 `components/app/Panel` 分组：标题 15px 半粗，可带图标与右侧操作；块之间 `gap-5`。
- 指标用 `StatCard`，切换筛选或显示方式用 `Segmented`，详情与编辑用右侧抽屉 `Sheet`。
- 居中弹窗只用于两类：确认（`lib/confirm.ts` 的 `confirmDialog`，由 Layout 里的 `ConfirmHost` 渲染，不用浏览器自带的 `window.confirm`），以及「左边填、右边看结果」的工具与预览（识别测试、识别规则、来源接入向导、整理预览、目录选择），这类弹窗按内容用 `max-w-xl` 到 `max-w-5xl`，窄屏改为上下排列。其余长表单放在页面或 `Sheet` 里。

## 加载、空状态与出错

- 加载中用 `ui/skeleton`；没有数据用 `EmptyState`，说明之后会出现什么。
- 读取失败用 `ErrorState`（原因 + 重试），不要显示成「还没有数据」或一直转圈；已有数据时刷新失败，用 `RefreshError` 在内容上方提示一行，数据照常显示。自动刷新失败不要反复弹 toast。
- toast 只用于用户刚做的操作的结果。

## 颜色

- 只使用语义变量：`bg-background`/`bg-card`/`bg-muted`、`text-foreground`/`text-muted-foreground`、`border-border`、`primary`/`success`/`warning`/`destructive`/`info`。
- 状态色统一走 `lib/status.ts` 的 `Tone` 与 `TONE_CLASS`：任务状态用 `StatusBadge`，连接与凭证状态用 `connectionMeta`，其他徽标用 `ToneBadge`。
- 不写死 `bg-white`、`text-gray-*`、十六进制颜色（品牌图标渐变、二维码白底除外），否则深色模式会出错。改完请在深色模式下检查一遍。

## 文字与间距

- 页面标题 22–24px 半粗；块标题 15px 半粗；正文 14px；辅助信息 12px `text-muted-foreground`。
- 数字使用 `tabular-nums`；圆角卡片 `rounded-xl`，控件 `rounded-md`/`rounded-lg`；卡片内边距 `p-5`（移动端 `p-4`）。
- 图标统一 lucide，常规尺寸 `size-4`。

## 表单

- 用 `components/app/Field`：标签在上（13px 中粗），说明在下（12px 弱化）；下拉框用 `app/Select`，开关用 `ui/switch`，二到五个选项的单选用 `Segmented` 或设置页的 `RadioCards`。
- 界面上只出现中文标签，不直接展示内部取值（如 `default`、`p115`、`json_schema`）：取值与状态的中文映射集中在 `lib/status.ts`，插件专属的取值放在插件自己的目录（如 `plugins/organizer/layouts.ts`）。提示语、操作完成的文案就近写在用到的地方。
- 下拉框的值为空或不在选项里时，`Select` 会显示「未选择」或标出这个值，不要让它悄悄显示成第一个选项。
- 有未保存修改时给出明确提示与保存入口，离开页面前确认；危险操作（删除、忽略、清除密钥）使用 `destructive` 并在必要时二次确认。

## 媒体内容

- 海报用 `MediaCover`（TMDB 海报，缺失时自动生成文字封面），媒体信息行用 `MediaMeta`。
- 时间默认显示相对时间（`lib/format.ts` 的 `relativeTime`），悬停 `title` 显示完整时间。

## 响应式

- 移动端优先：数据表在 `md` 以下改为每行一张卡片（见统计页 `AiPanels` 的 `MetricTable`），避免横向滚动；统计页按天的数字明细是图表的替代视图，保留表格、在框内滚动。
- 移动端可点击区域不小于 36px：写成 `min-h-9 sm:min-h-8`、`size-9 sm:size-7` 这样，桌面端用鼠标可以更紧凑（`Button size="sm"` 与 `Segmented` 已内置）。
- 底部标签栏覆盖在移动端页面底部，`Page` 已预留空间。
