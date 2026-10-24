/** 待审批页的数据整理：筛选排序、读取分享的提示、按建议分组、本地移除。不涉及请求与界面。 */
import type { IInboxResp } from '@/api/inbox';
import type { Tone } from '@/lib/status';
import type { IInboxItem, IShareProbe } from '@/lib/types';

export type InboxTab = 'pending_approval' | 'need_manual';
export type InboxSort = 'newest' | 'oldest' | 'confidence';

export interface InboxData {
  pending_approval: IInboxResp;
  need_manual: IInboxResp;
}

/** 按片名或来源筛选并排序（不改动原数组）。 */
export function visibleItems(items: IInboxItem[], query: string, sort: InboxSort): IInboxItem[] {
  const q = query.trim().toLowerCase();
  const list = q ? items.filter((i) => `${i.title ?? ''} ${i.source ?? ''}`.toLowerCase().includes(q)) : [...items];
  if (sort === 'oldest') list.sort((a, b) => a.created_at - b.created_at);
  else if (sort === 'confidence') list.sort((a, b) => a.confidence - b.confidence);
  else list.sort((a, b) => b.created_at - a.created_at);
  return list;
}

/** 消息没写集数的剧集读取分享的结果：集数来自分享时注明，失效、多季等转存时会转人工的情况提前标出。 */
export function shareBadge(share: IShareProbe | null): { tone: Tone; label: string; title: string } | null {
  if (!share?.status) return null;
  const note = share.note ?? '';
  switch (share.status) {
    case 'ok':
      return share.from_share ? { tone: 'info', label: '集数来自分享', title: `读取分享：${share.videos ?? 0} 个视频` } : null;
    case 'unavailable':
      return { tone: 'danger', label: '分享无法访问', title: note };
    case 'issue':
      return { tone: 'warning', label: note.split(/[：，]/)[0] || '转存时需人工', title: `${note}（转存时会转人工）` };
    case 'incomplete':
      return { tone: 'neutral', label: '分享内容较多，未读完', title: note };
    default:
      return null;  // 读取出错：启动时会重试，不打扰
  }
}

/** 「按建议处理」：所选条目里建议批准的批准、建议忽略的忽略；「需要你看一下」的留着。 */
export function adviceBatches(items: IInboxItem[], selected: ReadonlySet<string>): { approve: string[]; reject: string[] } {
  const chosen = items.filter((i) => selected.has(i.task_id));
  return {
    approve: chosen.filter((i) => i.advice?.action === 'approve').map((i) => i.task_id),
    reject: chosen.filter((i) => i.advice?.action === 'reject').map((i) => i.task_id),
  };
}

const mapItems = (data: InboxData, fn: (items: IInboxItem[]) => IInboxItem[]): InboxData => ({
  pending_approval: { ...data.pending_approval, items: fn(data.pending_approval.items) },
  need_manual: { ...data.need_manual, items: fn(data.need_manual.items) },
});

/** 操作成功的任务先从两个列表里移除，不等下一次刷新。 */
export function withoutTasks(data: InboxData, taskIds: readonly string[]): InboxData {
  const gone = new Set(taskIds);
  return mapItems(data, (items) => items.filter((i) => !gone.has(i.task_id)));
}

/** 订阅后同一部剧的待审批条目标为已订阅，不再提供「追这部剧」。 */
export function markSubscribed(data: InboxData, taskIds: readonly string[]): InboxData {
  const ids = new Set(taskIds);
  return mapItems(data, (items) => items.map((i) => (ids.has(i.task_id)
    ? { ...i, subscribed: true, actions: i.actions.filter((a) => a !== 'subscribe') } : i)));
}

/** 等 AI 重新措辞的建议：规则写的、还没请求过的（同一条的理由变了会再请求）。 */
export function advicePending(items: IInboxItem[], requested: ReadonlySet<string>, limit = 30): IInboxItem[] {
  return items.filter((i) => i.advice?.source === 'rule' && !requested.has(adviceKey(i))).slice(0, limit);
}

export const adviceKey = (item: IInboxItem) => `${item.task_id}:${item.advice?.text ?? ''}`;
