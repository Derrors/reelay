/** 内部取值 → 中文标签与色调：界面只展示这里的文案，不直接展示后端取值。插件专属的取值放在插件自己的目录。 */
import type { AdviceAction, TaskState } from '@/lib/types';

export type Tone = 'neutral' | 'info' | 'primary' | 'warning' | 'success' | 'danger';

/** 语义色调 → 徽标样式；页面只选色调，不直接写颜色。 */
export const TONE_CLASS: Record<Tone, { badge: string; dot: string; text: string; soft: string }> = {
  neutral: { badge: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/60', text: 'text-muted-foreground', soft: 'bg-muted' },
  info: { badge: 'bg-info/10 text-info', dot: 'bg-info', text: 'text-info', soft: 'bg-info/10' },
  primary: { badge: 'bg-primary/10 text-primary', dot: 'bg-primary', text: 'text-primary', soft: 'bg-primary/10' },
  warning: { badge: 'bg-warning/12 text-warning', dot: 'bg-warning', text: 'text-warning', soft: 'bg-warning/12' },
  success: { badge: 'bg-success/10 text-success', dot: 'bg-success', text: 'text-success', soft: 'bg-success/10' },
  danger: { badge: 'bg-destructive/10 text-destructive', dot: 'bg-destructive', text: 'text-destructive', soft: 'bg-destructive/10' },
};

/* ---------------- 任务 ---------------- */

export const STATE_META: Record<TaskState, { label: string; tone: Tone }> = {
  received: { label: '待识别', tone: 'info' },
  parsed: { label: '已识别', tone: 'info' },
  pending_approval: { label: '待确认', tone: 'warning' },
  queued: { label: '排队中', tone: 'primary' },
  rejected: { label: '已过滤', tone: 'neutral' },
  skipped: { label: '已忽略', tone: 'neutral' },
  saving: { label: '转存中', tone: 'primary' },
  stored: { label: '已转存', tone: 'primary' },
  organizing: { label: '整理中', tone: 'primary' },
  need_manual: { label: '待人工', tone: 'warning' },
  done: { label: '已入库', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
};

/** 还在流水线上推进的状态（识别、排队、转存、整理）。 */
export const ACTIVE_STATES: TaskState[] = ['received', 'parsed', 'queued', 'saving', 'stored', 'organizing'];

/** 任务列表的筛选分组：一个分组对应后端的一个或多个状态。 */
export const STATE_GROUPS: Array<{ key: string; label: string; states: TaskState[] }> = [
  { key: 'all', label: '全部', states: [] },
  { key: 'pending', label: '待确认', states: ['pending_approval'] },
  { key: 'active', label: '进行中', states: ACTIVE_STATES },
  { key: 'manual', label: '待人工', states: ['need_manual'] },
  { key: 'failed', label: '失败', states: ['failed'] },
  { key: 'done', label: '已入库', states: ['done'] },
  { key: 'filtered', label: '已过滤', states: ['rejected', 'skipped'] },
];

/** 置信度封顶原因（后端 confidence.caps）→ 给人看的说明。 */
export const CONFIDENCE_CAP_LABELS: Record<string, string> = {
  tmdb_unmatched: 'TMDB 未匹配',
  parse_fallback: 'AI 识别失败，已降级',
  no_title: '缺少片名',
};

/** 置信度的各项检查（confidence.checks）。 */
export const CONFIDENCE_CHECK_LABELS: Record<string, string> = {
  title: '片名', type: '类型与集数', year: '年份', tmdb: 'TMDB 匹配',
};

/** AI 决策：save / ask / skip */
export const DECISION_META: Record<string, { label: string; tone: Tone }> = {
  save: { label: '建议转存', tone: 'success' },
  ask: { label: '需要你确认', tone: 'warning' },
  skip: { label: '建议忽略', tone: 'neutral' },
};

/** 审批建议的色调；文案由后端给出。 */
export const ADVICE_TONE: Record<AdviceAction, Tone> = { approve: 'success', reject: 'neutral', review: 'warning' };

export const MEDIA_TYPE_LABELS: Record<string, string> = { movie: '电影', tv: '剧集', anime: '动漫' };

/* ---------------- 连接与运行 ---------------- */

/** 连接、凭证与后台进程的状态：总览、设置页「其他连接」与 115 连接卡片共用一套文案和色调。 */
const CONNECTION_META: Record<string, { label: string; tone: Tone }> = {
  connected: { label: '已连接', tone: 'success' },
  active: { label: '连接正常', tone: 'success' },
  running: { label: '运行中', tone: 'success' },
  connecting: { label: '连接中', tone: 'info' },
  unverified: { label: '已填写，待验证', tone: 'info' },
  unknown: { label: '等待检测', tone: 'info' },
  unobserved: { label: '未观察到状态', tone: 'neutral' },
  not_configured: { label: '未配置', tone: 'neutral' },
  not_logged_in: { label: '未登录', tone: 'warning' },
  stopped: { label: '已停止', tone: 'neutral' },
  stale: { label: '状态已过期', tone: 'warning' },
  disconnected: { label: '连接中断', tone: 'danger' },
  failed: { label: '连接失败', tone: 'danger' },
  expired: { label: '凭证已失效', tone: 'danger' },
  error: { label: '连接异常', tone: 'danger' },
};

export function connectionMeta(status: string): { label: string; tone: Tone } {
  return CONNECTION_META[status] ?? { label: '状态未知', tone: 'warning' };
}

// 系统运行模式：live / not_configured / not_running
export const modeLabels: Record<string, string> = {
  live: '正式运行',
  not_configured: '尚未配置',
  not_running: '后台未启动',
};

/** 页面与后端实时通道的连接方式。 */
export const REALTIME_LABELS: Record<'live' | 'polling' | 'offline', string> = {
  live: '实时', polling: '自动刷新', offline: '重新连接中',
};

/** AI 调用的用途（llm_calls.purpose）。 */
export const AI_PURPOSE_LABELS: Record<string, string> = {
  parse: '识别消息', preference: '按偏好判断', assist: '转人工前兜底', compile: '一句话生成规则', advice: '审批建议措辞',
  subscribe: '订阅解析', source_draft: '来源接入向导', parse_rules: '起草识别规则', source_quality: '来源质量评估',
  assistant: '控制 Bot 对话', explain: '失败解释', daily: '日报', other: '其他',
};

/* ---------------- 设置 ---------------- */

// 确认方式：manual / auto；是否发 Bot 通知是另一个开关（bot_notify）
export const approvalLabels: Record<string, string> = {
  manual: '人工确认',
  auto: 'AI 处理',
};

// 审批超时动作：remind / skip
export const timeoutActionLabels: Record<string, string> = {
  remind: '再次提醒我',
  skip: '自动跳过',
};

// LLM 输出格式
export const llmFormatLabels: Record<string, string> = {
  json_schema: '结构化（推荐，最稳定）',
  json_object: '普通 JSON（兼容旧模型）',
};

/** 推理强度（llm.reasoning_effort），空值表示不传。 */
export const REASONING_EFFORTS: Array<{ value: string; label: string }> = [
  { value: '', label: '默认（不传，由模型决定）' }, { value: 'none', label: '关闭推理（none）' },
  { value: 'minimal', label: '最低（minimal）' }, { value: 'low', label: '低（low）' },
  { value: 'medium', label: '中（medium）' }, { value: 'high', label: '高（high）' },
  { value: 'xhigh', label: '更高（xhigh）' }, { value: 'max', label: '最高（max）' },
];

export const LOG_LEVELS: Array<{ value: string; label: string }> = [
  { value: 'DEBUG', label: '调试（DEBUG）' }, { value: 'INFO', label: '一般（INFO）' },
  { value: 'WARNING', label: '警告（WARNING）' }, { value: 'ERROR', label: '错误（ERROR）' },
];

/** 版本偏好（release）的可选值。 */
export const RELEASE_OPTIONS: Record<'subtitles' | 'audio' | 'sources' | 'hdr', Array<{ value: string; label: string }>> = {
  subtitles: [
    { value: 'chs', label: '简体中字' }, { value: 'cht', label: '繁体中字' }, { value: 'eng', label: '英字' },
    { value: 'jpn', label: '日字' }, { value: 'kor', label: '韩字' },
  ],
  audio: [
    { value: 'mandarin', label: '国语' }, { value: 'cantonese', label: '粤语' }, { value: 'english', label: '英语' },
    { value: 'japanese', label: '日语' }, { value: 'korean', label: '韩语' },
  ],
  sources: [
    { value: 'remux', label: 'Remux' }, { value: 'bluray', label: '蓝光' }, { value: 'web-dl', label: 'WEB-DL' },
    { value: 'webrip', label: 'WEBRip' }, { value: 'hdtv', label: 'HDTV' }, { value: 'dvd', label: 'DVD' },
  ],
  hdr: [
    { value: 'any', label: '不在意' }, { value: 'prefer', label: '优先 HDR / 杜比视界' }, { value: 'avoid', label: '避开 HDR（设备不支持）' },
  ],
};

/** 能力槽位 → 中文名 */
export const SLOT_LABELS: Record<string, string> = {
  source: '消息接入方式', parser: '识别方式', decision: '转存方式', storage: '网盘', notify: '通知渠道',
};

/** 能力实现的内部名 → 中文名；不在这里的实现用扩展目录里的 label。 */
export const PROVIDER_LABELS: Record<string, Record<string, string>> = {
  decision: { default: '规则判断', subscription: '规则 + 订阅放行（推荐）', llm: 'AI 按偏好判断（含订阅放行）' },
  parser: { llm: 'AI 识别（推荐）', regex: '规则识别' },
  source: { telegram: 'Telegram 个人账号' },
  storage: { p115: '115 网盘' },
  notify: { console: '控制 Bot / 日志' },
};

/** 运行时机（hook 阶段 / 事件名）→ 中文 */
export const STAGE_LABELS: Record<string, string> = {
  after_parse: '识别完成后', before_save: '转存前', after_save: '转存完成后',
  on_message: '收到消息时', on_identified: '识别出资源后', on_rejected: '判为广告后',
  on_saved: '入库后', on_failed: '失败时', on_daily: '每日',
};

/** 插件字段与 TMDB 语言的可选值 → 中文 */
export const CHOICE_LABELS: Record<string, string> = {
  'zh-CN': '简体中文', 'zh-TW': '繁體中文', 'en-US': 'English',
};

/** 来源识别规则的字段（见 reelay/core/parse_rules.py）。 */
export const PARSE_RULE_FIELDS = ['title', 'year', 'season', 'episodes', 'resolution', 'size'] as const;
export type ParseRuleField = typeof PARSE_RULE_FIELDS[number];
export const PARSE_RULE_LABELS: Record<ParseRuleField, string> = {
  title: '片名', year: '年份', season: '季', episodes: '集', resolution: '分辨率', size: '大小',
};
