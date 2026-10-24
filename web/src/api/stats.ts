import { get } from './request';

export interface IStatsResp {
  counts: Record<string, number>;
  daily: Array<{ d: string; state: string; n: number }>;
  /** 按片名合并的任务数，done 是其中已入库的。 */
  top_titles: Array<{ title: string; count: number; done: number }>;
  estimated_done_gb: number;
}

export const statsApi = {
  get: (days = 30) => get<IStatsResp>(`stats?days=${days}`),
};
