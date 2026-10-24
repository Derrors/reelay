// ===== AI 能力：审批洞察、调用记录、一句话规则、订阅、收件箱建议、来源接入与质量、媒体库问答 =====
// AI 起草的内容（规则、来源配置）只返回草案与改动清单，写入仍走带版本号的 saveSections，由用户确认。
import type { IAdvice } from '@/lib/types';
import { apiUrl, ApiError, reportUnauthorized } from './client';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(apiUrl(path), {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!r.ok) {
    let msg = `请求失败（${r.status}）`;
    try {
      const detail: unknown = (await r.json()).detail;
      if (typeof detail === 'string') msg = detail;
      else if (Array.isArray(detail)) msg = detail.map((x: { msg?: string }) => x.msg ?? '输入无效').join('；');
    } catch { /* 非 JSON 响应沿用状态码 */ }
    if (r.status === 401) {
      msg = '登录已失效，请重新登录控制台';
      reportUnauthorized();
    }
    throw new ApiError(msg, r.status);
  }
  return r.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });

/** 按来源的人工裁决统计与「改为自动转存」建议。 */
export interface ISourceInsight {
  source: string;
  approval_mode: string;
  decisions: number;
  approved: number;
  rejected: number;
  corrected: number;
  /** 有建议时按建议处理的次数。 */
  advised: number;
  followed: number;
  approve_rate: number | null;
  correct_rate: number | null;
  suggest_auto: boolean;
  reason: string;
}
export interface IInsights {
  window: number;
  rule: { min_decisions: number; approve_rate: number; max_correct_rate: number };
  sources: ISourceInsight[];
}

export interface ILlmUsagePurpose {
  purpose: string;
  calls: number;
  ok: number;
  invalid: number;
  refused: number;
  error: number;
  avg_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
}
export interface ILlmUsage {
  since: number;
  total: { calls: number; ok: number; invalid: number; refused: number; error: number;
    prompt_tokens: number; completion_tokens: number };
  purposes: ILlmUsagePurpose[];
  daily: Array<{ d: string; calls: number; tokens: number }>;
  models: string[];
}

/** 规则改动的一项：section 如「默认规则」「剧集分类」「版本偏好」。 */
export interface IRuleChange {
  section: string;
  kind: 'add' | 'remove' | 'change';
  text: string;
}
export interface ICompileResult {
  summary: string;
  unsupported: string[];
  changes: IRuleChange[];
  /** 确认后原样交给 saveSections 的配置块。 */
  sections: Record<string, unknown>;
  revision: string;
}

export interface ISubscription {
  title: string;
  season: number;
  total: number | null;
  tmdbid: number | null;
  /** false 只做缺集提醒，不自动放行新集。 */
  auto: boolean;
  have: number[];
  missing: number[];
  aired: number | null;
  /** 电视剧 / 动漫 / 综艺 / 纪录片 / 少儿（按 TMDB 题材）。 */
  category: string;
  /** 播完且全部入库后由每日巡检标记：不再自动转存与巡检缺集。 */
  finished: { date: string; episodes: number } | null;
}
export interface ISubscriptionSuggestion {
  title: string;
  season: number;
  tmdbid: number | null;
  count: number;
  text: string;
}
/** 不再接收的作品：同一部作品之后的消息识别后直接忽略（在 Telegram 审批卡片点「不再接收」添加）。 */
export interface IIgnoredWork {
  title: string;
  tmdbid: number | null;
  kind: 'movie' | 'tv' | null;
}

export interface ISubscriptionsResp {
  items: ISubscription[];
  suggestions: ISubscriptionSuggestion[];
  /** 转存方式不支持订阅放行的规则（「默认规则」或来源名）。 */
  inactive: string[];
}
export interface IShowCandidate {
  tmdbid: number;
  title: string;
  original_title: string | null;
  year: number | null;
  poster_path: string | null;
  overview: string;
  seasons: Array<{ season: number; episodes: number | null; air_date: string | null }>;
}
export interface IResolveResp {
  query: { title: string; season: number | null; year: number | null };
  candidates: IShowCandidate[];
  /** 是否配置了 TMDB；没有时只能按片名订阅。 */
  tmdb: boolean;
}
export interface IAddSubscriptionResp {
  subscription: { title: string; season: number; tmdbid?: number; total?: number; auto?: boolean };
  /** 待审批里属于这部剧这一季的任务。 */
  pending: string[];
}

export interface ISourceDraft {
  draft: Record<string, unknown>;
  summary: string;
  notes: string[];
  results: Array<{
    preview: string; decision: string; confidence: number | null;
    title: string | null; media_type: string | null; season: number | null; episodes: number[] | null;
    resolution: string | null; reason: string | null;
  }>;
  /** 用草案的关键词过滤核对过的、这个来源批准过的消息数，以及其中会被挡掉的。 */
  approved_checked: number;
  blocked_approved: Array<{ preview: string; reason: string }>;
  /** 配置里已有同名来源，加入时会替换它。 */
  exists: boolean;
}

/** 来源识别规则的字段（见 reelay/core/parse_rules.py）。 */
export const PARSE_RULE_FIELDS = ['title', 'year', 'season', 'episodes', 'resolution', 'size'] as const;
export type ParseRuleField = typeof PARSE_RULE_FIELDS[number];
export const PARSE_RULE_LABELS: Record<ParseRuleField, string> = {
  title: '片名', year: '年份', season: '季', episodes: '集', resolution: '分辨率', size: '大小',
};

export interface IParseRulesPreview {
  /** 去掉留空字段后的规则。 */
  rules: Partial<Record<ParseRuleField, string>>;
  /** 每条样例按规则识别的结果；from_rules 是来源规则匹配到的字段，其余来自通用规则。 */
  results: Array<{
    preview: string; title: string | null; year: number | null; season: number | null; episodes: number[];
    resolution: string | null; size: string | null; media_type: string | null; from_rules: ParseRuleField[];
  }>;
  /** AI 起草时写的提醒。 */
  notes?: string[];
}

export interface ISourceMessages {
  messages: Array<{ text: string; urls: string[] }>;
  /** telegram：主进程实时读取；history：读取失败，用的是这个来源已经收到的消息（problem 是原因）。 */
  origin: 'telegram' | 'history';
  problem: string | null;
}

export interface ISourceQuality {
  source: string;
  received: number;
  ads: number;
  filtered: number;
  duplicates: number;
  done: number;
  media: number;
  ad_rate: number | null;
  filter_rate: number | null;
  duplicate_rate: number | null;
  tmdb_match_rate: number | null;
  fallback_rate: number | null;
  /** 影视资源中分享在转存前已过期或取消的比例。 */
  unavailable: number;
  unavailable_rate: number | null;
  decisions: number;
  approve_rate: number | null;
  correct_rate: number | null;
  /** 入库资源的典型清晰度。 */
  resolution: string | null;
  flags: string[];
}
export interface IQualityAdvice {
  items: Array<{ source: string; summary: string; suggestions: string[]; add_block_keywords: string[];
    dropped_keywords: string[] }>;
  changes: IRuleChange[];
  sections: Record<string, unknown>;
  revision: string;
}

/** 测试 AI 服务连通性的结果。 */
export interface ILlmTest {
  ok: boolean; model: string; seconds: number;
  reply?: string; prompt_tokens?: number | null; completion_tokens?: number | null; error?: string;
}
/** 地址、模型与推理强度用表单里的值（可以还没保存）；密钥总是用已保存的。 */
export interface ILlmForm { base_url?: string; model?: string; reasoning_effort?: string }

/** 某个模型支持的推理强度：supported / unsupported（接口明确拒绝）/ unknown（超时等，未能确认）。efforts 为 null 表示没检测过。 */
export interface ILlmEfforts { model: string; efforts: Record<string, 'supported' | 'unsupported' | 'unknown'> | null; note?: string | null }

export const ai = {
  llmEfforts: (form: ILlmForm) =>
    req<ILlmEfforts>(`ai/efforts?${new URLSearchParams({ base_url: form.base_url ?? '', model: form.model ?? '' })}`),
  probeLlmEfforts: (form: ILlmForm) => post<ILlmEfforts>('ai/efforts', form),
  llmModels: (form: ILlmForm) => post<{ models: string[] }>('ai/models', form),
  llmTest: (form: ILlmForm) => post<ILlmTest>('ai/test', form),
  insights: () => req<IInsights>('insights'),
  enableAuto: (source: string) =>
    post<{ config: Record<string, unknown>; revision: string }>('insights/auto-approve', { source }),
  usage: (days = 30) => req<ILlmUsage>(`ai/usage?days=${days}`),
  compileRules: (text: string, scope: string) => post<ICompileResult>('ai/compile-rules', { text, scope }),
  subscriptions: () => req<ISubscriptionsResp>('subscriptions'),
  resolveShow: (text: string) => post<IResolveResp>('subscriptions/resolve', { text }),
  subscribe: (item: { title: string; season?: number | null; tmdbid?: number | null; total?: number | null; auto?: boolean }) =>
    post<IAddSubscriptionResp>('subscriptions', item),
  subscribeFromTask: (taskId: string) => post<IAddSubscriptionResp>(`subscriptions/from-task/${taskId}`),
  unsubscribe: (item: { title: string; season: number; tmdbid: number | null }) =>
    post<{ ok: boolean }>('subscriptions/remove', item),
  reopenSubscription: (item: { title: string; season: number; tmdbid: number | null }) =>
    post<{ ok: boolean }>('subscriptions/reopen', item),
  /** 订阅建议「不用了」：这部剧这一季不再出现在建议里（Telegram 上同样不再提示）。 */
  dismissSuggestion: (item: { title: string; season: number; tmdbid: number | null }) =>
    post<{ ok: boolean }>('subscriptions/suggestions/dismiss', item),
  ignoredWorks: () => req<{ items: IIgnoredWork[] }>('ignored-works'),
  unignoreWork: (item: IIgnoredWork) => post<{ ok: boolean }>('ignored-works/remove', item),
  /** AI 措辞的建议理由（按事实缓存）；AI 不可用时返回空对象。 */
  advice: (taskIds: string[]) => post<{ items: Record<string, IAdvice> }>('inbox/advice', { task_ids: taskIds }),
  draftSource: (bot: string, samples: string[]) => post<ISourceDraft>('sources/draft', { bot, samples }),
  sourceMessages: (bot: string, limit = 10) =>
    req<ISourceMessages>(`sources/messages?bot=${encodeURIComponent(bot)}&limit=${limit}`),
  draftParseRules: (bot: string, samples: string[]) =>
    post<IParseRulesPreview>('sources/parse-rules/draft', { bot, samples }),
  previewParseRules: (rules: Record<string, string>, samples: string[]) =>
    post<IParseRulesPreview>('sources/parse-rules/preview', { rules, samples }),
  sourceQuality: (days = 30) => req<{ days: number; sources: ISourceQuality[] }>(`sources/quality?days=${days}`),
  sourceQualityAdvice: (days = 30) => post<IQualityAdvice>(`sources/quality/advice?days=${days}`),
};

/** 比例 → 「95%」；没有数据时为「—」。 */
export const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;
