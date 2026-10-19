// EXPORTS: STATE_META, formatDateTime
import type { TaskState } from '@/lib/types';

export const connectionLabels: Record<string, string> = {
  connected: '已连接', connecting: '连接中', disconnected: '连接中断', failed: '连接失败', stopped: '已停止',
  stale: '状态已过期', unverified: '已填写，待验证', not_configured: '未配置', unobserved: '未观察到状态',
};

// 系统运行模式：simulation / live / not_configured / not_running
export const modeLabels: Record<string, string> = {
  simulation: '模拟运行',
  live: '正式运行',
  not_configured: '尚未配置',
  not_running: '后台未启动',
};

// 审批模式：manual / auto / default_pass
export const approvalLabels: Record<string, string> = {
  manual: '每条都需我确认',
  auto: '自动通过并通知我',
  default_pass: '全自动，无需打扰我',
};

export const approvalShortLabels: Record<string, string> = {
  manual: '人工确认',
  auto: '自动通过',
  default_pass: '静默通过',
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

// AI 决策：save / ask / skip
export const decisionLabels: Record<string, string> = {
  save: '建议转存',
  ask: '需要你确认',
  skip: '建议忽略',
};

export const STATE_META: Record<TaskState, { label: string; className: string }> = {
  received: { label: '待解析', className: 'bg-info/10 text-info border-info/20' },
  parsed: { label: '已识别', className: 'bg-info/10 text-info border-info/20' },
  pending_approval: { label: '待你确认', className: 'bg-warning/10 text-warning border-warning/20' },
  queued: { label: '排队转存中', className: 'bg-primary/10 text-primary border-primary/20' },
  rejected: { label: '已过滤', className: 'bg-muted text-muted-foreground border-border' },
  skipped: { label: '已忽略', className: 'bg-muted text-muted-foreground border-border' },
  saving: { label: '正在转存', className: 'bg-primary/10 text-primary border-primary/20' },
  stored: { label: '已转存', className: 'bg-info/10 text-info border-info/20' },
  organizing: { label: '正在整理', className: 'bg-primary/10 text-primary border-primary/20' },
  need_manual: { label: '待人工处理', className: 'bg-warning/10 text-warning border-warning/20' },
  done: { label: '已完成', className: 'bg-success/10 text-success border-success/20' },
  failed: { label: '失败', className: 'bg-destructive/10 text-destructive border-destructive/20' },
};

export function formatDateTime(value: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value * 1000));
}
