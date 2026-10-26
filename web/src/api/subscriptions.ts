// 追剧订阅与「不再接收」的作品（存在数据库里，不属于配置文件）
import { get, post } from './request';

export interface ISubscription {
  /** 数据库里的 ID：取消、继续订阅时按它定位。 */
  id: number;
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
  id: number;
  title: string;
  tmdbid: number | null;
  kind: 'movie' | 'tv' | null;
}

export interface ISubscriptionsResp {
  items: ISubscription[];
  suggestions: ISubscriptionSuggestion[];
  /** 放行方式不支持订阅放行的规则（「默认规则」或来源名）。 */
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

/** 一部剧的一季：已有订阅带上 id；订阅建议只有片名、季与 tmdbid。 */
type ShowKey = { id?: number; title: string; season: number; tmdbid: number | null };

export const subscriptionApi = {
  list: () => get<ISubscriptionsResp>('subscriptions'),
  resolve: (text: string) => post<IResolveResp>('subscriptions/resolve', { text }),
  add: (item: { title: string; season?: number | null; tmdbid?: number | null; total?: number | null; auto?: boolean }) =>
    post<IAddSubscriptionResp>('subscriptions', item),
  addFromTask: (taskId: string) => post<IAddSubscriptionResp>(`subscriptions/from-task/${taskId}`),
  remove: (item: ShowKey) => post<{ ok: boolean }>('subscriptions/remove', item),
  reopen: (item: ShowKey) => post<{ ok: boolean }>('subscriptions/reopen', item),
  /** 订阅建议「不用了」：这部剧这一季不再出现在建议里（Telegram 上同样不再提示）。 */
  dismissSuggestion: (item: ShowKey) => post<{ ok: boolean }>('subscriptions/suggestions/dismiss', item),
  ignoredWorks: () => get<{ items: IIgnoredWork[] }>('ignored-works'),
  unignoreWork: (item: IIgnoredWork) => post<{ ok: boolean }>('ignored-works/remove', item),
};
