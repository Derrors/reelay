// EXPORTS: STATE_META, formatDateTime
import type { TaskState } from '@/lib/types';

export const connectionLabels: Record<string, string> = {
  connected: '已连接', connecting: '连接中', disconnected: '连接中断', failed: '连接失败', stopped: '已停止',
  stale: '状态已过期', unverified: '已填写，待验证', not_configured: '未配置', unobserved: '未观察到状态',
  expired: '登录已失效', error: '探活失败',
};

// 系统运行模式：simulation / live / not_configured / not_running
export const modeLabels: Record<string, string> = {
  simulation: '模拟运行',
  live: '正式运行',
  not_configured: '尚未配置',
  not_running: '后台未启动',
};

// 确认方式：manual / auto；是否发 Bot 通知是另一个开关（bot_notify）
export const approvalLabels: Record<string, string> = {
  manual: '人工确认',
  auto: 'AI 处理',
};

export const approvalShortLabels: Record<string, string> = approvalLabels;

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

// AI 决策：save / ask / skip
export const decisionLabels: Record<string, string> = {
  save: '建议转存',
  ask: '需要你确认',
  skip: '建议忽略',
};

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

export const STATE_META: Record<TaskState, { label: string; tone: Tone; className: string }> = {
  received: { label: '待识别', tone: 'info', className: TONE_CLASS.info.badge },
  parsed: { label: '已识别', tone: 'info', className: TONE_CLASS.info.badge },
  pending_approval: { label: '待确认', tone: 'warning', className: TONE_CLASS.warning.badge },
  queued: { label: '排队中', tone: 'primary', className: TONE_CLASS.primary.badge },
  rejected: { label: '已过滤', tone: 'neutral', className: TONE_CLASS.neutral.badge },
  skipped: { label: '已忽略', tone: 'neutral', className: TONE_CLASS.neutral.badge },
  saving: { label: '转存中', tone: 'primary', className: TONE_CLASS.primary.badge },
  stored: { label: '已转存', tone: 'primary', className: TONE_CLASS.primary.badge },
  organizing: { label: '整理中', tone: 'primary', className: TONE_CLASS.primary.badge },
  need_manual: { label: '待人工', tone: 'warning', className: TONE_CLASS.warning.badge },
  done: { label: '已入库', tone: 'success', className: TONE_CLASS.success.badge },
  failed: { label: '失败', tone: 'danger', className: TONE_CLASS.danger.badge },
};

/** 任务列表的筛选分组：一个分组对应后端的一个或多个状态。 */
export const STATE_GROUPS: Array<{ key: string; label: string; states: TaskState[] }> = [
  { key: 'all', label: '全部', states: [] },
  { key: 'pending', label: '待确认', states: ['pending_approval'] },
  { key: 'active', label: '进行中', states: ['received', 'parsed', 'queued', 'saving', 'stored', 'organizing'] },
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

export function formatDateTime(value: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value * 1000));
}
