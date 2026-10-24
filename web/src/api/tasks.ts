import type { IParsedResource, ITaskDetail, ITaskSummary } from '@/lib/types';
import { get, patch, post, query } from './request';

/** 任务操作的接口（POST /api/tasks/<id>/<endpoint>）。accept-partial：转存只到一部分时按已到的文件继续；
 *  abandon：放弃这次转存，删除暂存目录后忽略。 */
export type TaskEndpoint = 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize' | 'reconcile'
  | 'accept-partial' | 'abandon';

export interface ITaskFilters {
  /** 可传逗号分隔的多个状态。 */
  state?: string;
  source?: string;
  query?: string;
  limit?: number;
  offset?: number;
}

export interface IBatchResult {
  done: number;
  failed: Array<{ task_id: string; error: string }>;
}

export const taskApi = {
  list: (filters: ITaskFilters = {}) => get<{ items: ITaskSummary[] }>(`tasks${query({ ...filters })}`),
  get: (taskId: string) => get<ITaskDetail>(`tasks/${taskId}`),
  act: (taskId: string, endpoint: TaskEndpoint, body?: unknown) =>
    post<{ ok: boolean }>(`tasks/${taskId}/${endpoint}`, body),
  /** 修正识别结果，返回更新后的详情。 */
  correct: (taskId: string, values: Partial<IParsedResource>) =>
    patch<ITaskDetail>(`tasks/${taskId}/parsed`, { values }),
  /** saveDirAlias 只对 approve 生效：统一改存到该目录。 */
  batch: (taskIds: string[], action: 'approve' | 'reject' | 'retry', saveDirAlias?: string) =>
    post<IBatchResult>('tasks/batch', { task_ids: taskIds, action, save_dir_alias: saveDirAlias || null }),
};
