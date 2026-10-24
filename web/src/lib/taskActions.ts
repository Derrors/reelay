/** 任务操作：服务端在 actions 里给出此刻允许的操作，界面只按这个列表显示按钮，不自己推断。 */
import type { TaskEndpoint } from '@/api/tasks';
import type { ConflictChoice } from '@/lib/conflict';
import type { TaskActionName } from '@/lib/types';

/** 走 POST /api/tasks/<id>/<endpoint> 的操作；correct（修正识别）与 subscribe（订阅）各有自己的接口。 */
export type PostedAction = Exclude<TaskActionName, 'correct' | 'subscribe'>;

export const ACTION_ENDPOINT: Record<PostedAction, TaskEndpoint> = {
  approve: 'approve', reject: 'reject', dismiss: 'reject', retry: 'retry', reparse: 'reparse',
  reorganize: 'reorganize', resolve_conflict: 'reorganize', reconcile: 'reconcile',
  accept_partial: 'accept-partial', abandon: 'abandon',
};

/** 操作成功后的提示。 */
export const ACTION_DONE: Record<PostedAction, string> = {
  approve: '已确认，开始转存', reject: '已忽略这条资源', dismiss: '已忽略这个任务', retry: '已重新排队',
  reparse: '已回到识别队列', reorganize: '已回到整理队列', resolve_conflict: '已回到整理队列',
  reconcile: '已排队核验转存', accept_partial: '已排队：按已到的文件继续整理', abandon: '已排队：放弃并删除暂存目录',
};

/** 操作的请求体：确认与重新整理可指定保存目录（空 = 自动分类 / 保持当前目录），处理冲突要带上选择。 */
export function actionBody(action: PostedAction, options: { dir?: string; conflict?: ConflictChoice } = {}): unknown {
  if (action === 'resolve_conflict') return { conflict: options.conflict };
  if (action === 'approve' || action === 'reorganize') return { save_dir_alias: options.dir || null };
  return undefined;
}

export const allows = (actions: readonly TaskActionName[] | undefined, action: TaskActionName) =>
  Boolean(actions?.includes(action));
