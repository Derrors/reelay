// 监听来源：识别测试、AI 接入向导、识别规则、来源质量与人工裁决统计
// AI 起草的内容只返回草案与改动清单，写入仍走带版本号的 configApi.saveSections，由用户确认。
import type { ParseRuleField } from '@/lib/status';
import type { IParsedResource, IRuleChange } from '@/lib/types';
import { get, post, query } from './request';

export interface IConfidence {
  score: number;
  checks?: Record<string, boolean>;
  caps?: string[];
  /** 非影视消息只有这一项。 */
  note?: string;
}

/** 识别测试：与真实管线一致的解析、置信度与决策。 */
export interface ITestParseResp {
  parsed: IParsedResource | null;
  decision: string;
  confidence: IConfidence | null;
  trail: string[];
}

/** 按来源的人工裁决统计与「改为 AI 处理」建议。 */
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
  /** 同一张卡片上的来源（合起来统计，source 是其中第一个）。 */
  bots?: string[];
  reason: string;
}

export interface IInsights {
  window: number;
  rule: { min_decisions: number; approve_rate: number; max_correct_rate: number };
  sources: ISourceInsight[];
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

export const sourceApi = {
  /** draft：来源卡片上还没保存的设置（sources[] 的一项），省略时按已保存的来源规则。 */
  testParse: (text: string, urls: string[], source?: string, draft?: Record<string, unknown>) =>
    post<ITestParseResp>('sources/test-parse', { text, urls, source, draft }),
  draft: (bot: string, samples: string[]) => post<ISourceDraft>('sources/draft', { bot, samples }),
  messages: (bot: string, limit = 10) => get<ISourceMessages>(`sources/messages${query({ bot, limit })}`),
  draftParseRules: (bot: string, samples: string[]) => post<IParseRulesPreview>('sources/parse-rules/draft', { bot, samples }),
  previewParseRules: (rules: Record<string, string>, samples: string[]) =>
    post<IParseRulesPreview>('sources/parse-rules/preview', { rules, samples }),
  quality: (days = 30) => get<{ days: number; sources: ISourceQuality[] }>(`sources/quality?days=${days}`),
  qualityAdvice: (days = 30) => post<IQualityAdvice>(`sources/quality/advice?days=${days}`),
  insights: () => get<IInsights>('insights'),
  /** 把来源的确认方式改为 AI 处理（直接写入配置）。 */
  enableAuto: (source: string) => post<{ config: Record<string, unknown>; revision: string }>('insights/auto-approve', { source }),
};
