import { toast } from 'sonner';
import { taskApi } from '@/api/tasks';
import { errorMessage } from '@/lib/utils';

/** 订阅成功的提示；待审批里还有这部剧这一季的条目时，提供「一并转存」（按各自的分类存放）。
 *  onApproved 收到实际转存了的任务，用于刷新页面上的列表。 */
export function announceSubscription(
  show: { title: string; season: number },
  pending: string[],
  onApproved?: (taskIds: string[]) => void,
) {
  const approve = async () => {
    try {
      const r = await taskApi.batch(pending, 'approve');
      const failed = new Set(r.failed.map((f) => f.task_id));
      onApproved?.(pending.filter((id) => !failed.has(id)));
      if (r.failed.length) toast.error(`${r.done} 条已转存，${r.failed.length} 条失败：${r.failed[0].error}`);
      else toast.success(`已批准 ${r.done} 条，开始转存`);
    } catch (e) {
      toast.error(errorMessage(e, '批量转存失败'));
    }
  };
  toast.success(`已订阅《${show.title}》第 ${show.season} 季，之后的新集会自动转存`, pending.length ? {
    description: `待审批里还有 ${pending.length} 条属于这部剧`,
    action: { label: `一并转存 ${pending.length} 条`, onClick: () => { void approve(); } },
    duration: 10000,
  } : undefined);
}
