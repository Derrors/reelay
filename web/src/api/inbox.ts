import type { IAdvice, IInboxItem } from '@/lib/types';
import { get, post, query } from './request';

export type InboxState = 'pending_approval' | 'need_manual';

export interface IInboxResp {
  items: IInboxItem[];
  /** 保存目录别名，以及别名在网盘里的真实路径（还没记录的没有）。 */
  save_dirs: string[];
  save_dir_paths: Record<string, string>;
}

export interface ISaveDirs {
  dirs: string[];
  paths: Record<string, string>;
}

export const inboxApi = {
  list: (state: InboxState = 'pending_approval', limit?: number) => get<IInboxResp>(`inbox${query({ state, limit })}`),
  /** 保存目录：收件箱接口顺带返回，只取 1 条，比读取整份配置轻得多。 */
  saveDirs: () => inboxApi.list('pending_approval', 1).then((r): ISaveDirs => ({ dirs: r.save_dirs, paths: r.save_dir_paths })),
  /** AI 措辞的建议理由（按事实缓存）；AI 不可用时返回空对象。 */
  advice: (taskIds: string[]) => post<{ items: Record<string, IAdvice> }>('inbox/advice', { task_ids: taskIds }),
};
