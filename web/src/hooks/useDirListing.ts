import { useState } from 'react';
import { toast } from 'sonner';
import { storageApi, type IDirItem } from '@/api/storage';
import { useAsync } from '@/hooks/useAsync';
import { errorMessage } from '@/lib/utils';

export type Crumb = { id: string; name: string };

/**
 * 逐级浏览 115 目录（115 目录页与目录选择弹窗共用）：进入目录、面包屑路径、分页加载更多。
 * 路径优先用 115 返回的真实路径（按编号定位时也能显示正确的目录名）；根目录由界面单独显示，这里不含。
 */
export function useDirListing(initial: { cid: string; path: Crumb[] } = { cid: '0', path: [] }) {
  const [target, setTarget] = useState(initial);
  const first = useAsync(() => storageApi.dirs(target.cid), [target.cid], '目录查询失败');
  const [more, setMore] = useState<{ cid: string; items: IDirItem[]; next: number | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const page = first.stale ? undefined : first.data;
  const extra = more?.cid === target.cid ? more : null;
  const serverPath = (page?.path ?? []).filter((p) => p.id !== '0').map((p) => ({ id: p.id, name: p.name }));
  const path = page && (serverPath.length || target.cid === '0') ? serverPath : target.path;

  const open = (cid: string, crumbs: Crumb[]) => {
    setMore(null);
    setTarget({ cid, path: crumbs });
  };

  const loadMore = async () => {
    const next = extra ? extra.next : page?.next_offset ?? null;
    if (next === null || loadingMore) return;
    const cid = target.cid;
    setLoadingMore(true);
    try {
      const r = await storageApi.dirs(cid, next);
      setMore((prev) => ({ cid, items: [...(prev?.cid === cid ? prev.items : []), ...r.items], next: r.next_offset }));
    } catch (e) {
      toast.error(errorMessage(e, '目录查询失败'));
    } finally {
      setLoadingMore(false);
    }
  };

  return {
    cid: target.cid,
    path,
    /** 当前目录的第一页还没读到。 */
    loading: first.loading,
    ready: page !== undefined,
    error: first.error,
    /** 进入新目录、结果还没到时，仍是上一个目录的列表（由界面决定变淡还是显示骨架）。 */
    items: page ? [...page.items, ...(extra?.items ?? [])] : first.data?.items ?? [],
    next: extra ? extra.next : page?.next_offset ?? null,
    loadingMore,
    open,
    loadMore,
    reload: () => { setMore(null); first.reload(); },
  };
}
