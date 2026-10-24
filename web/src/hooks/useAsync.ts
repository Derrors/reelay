import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react';
import { errorMessage } from '@/lib/utils';

export interface AsyncResult<T> {
  /** 最近一次成功取到的数据（可能属于旧的 deps，见 stale）。 */
  data: T | undefined;
  /** 最近一次请求的错误；之后成功一次就清空。 */
  error: string;
  /** 当前 deps 的最新一次请求还没有结果。 */
  loading: boolean;
  /** data 是旧 deps 取到的（如换了搜索词，新结果还在路上）。 */
  stale: boolean;
  /** 按当前 deps 重新取一次（实时刷新、重试）。 */
  reload: () => void;
  /** 本地修改已取到的数据（如操作成功后先从列表里移除）。 */
  mutate: (update: (data: T | undefined) => T | undefined) => void;
}

interface Settled<T> {
  /** 这份结果对应的 deps 与第几次 reload。 */
  key: string | null;
  reloads: number;
  data: T | undefined;
  error: string;
}

/**
 * 取数并跟踪加载、错误与新旧：deps 变化时重新取，旧 deps 的结果到达后直接丢弃；
 * 同一 deps 的多次 reload 按发出顺序生效，慢的旧请求不会盖掉新结果，频繁刷新时也不会一直等不到结果。
 * deps 只放可序列化的简单值（字符串、数字、布尔、null）。
 */
export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[], fallback = '读取失败'): AsyncResult<T> {
  const key = JSON.stringify(deps);
  const [reloads, setReloads] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: null, reloads: 0, data: undefined, error: '' });
  const latest = useRef({ key, sent: 0, applied: 0 });
  const run = useEffectEvent(load);
  const describe = useEffectEvent((e: unknown) => errorMessage(e, fallback));

  useEffect(() => {
    const seq = ++latest.current.sent;
    latest.current.key = key;
    const apply = (next: Pick<Settled<T>, 'data' | 'error'> | ((prev: Settled<T>) => Pick<Settled<T>, 'data' | 'error'>)) => {
      if (latest.current.key !== key || seq <= latest.current.applied) return;
      latest.current.applied = seq;
      setSettled((prev) => ({ key, reloads, ...(typeof next === 'function' ? next(prev) : next) }));
    };
    run().then(
      (data) => apply({ data, error: '' }),
      // 刷新失败时保留同一 deps 已有的数据；换了 deps 就不能把旧结果当成新的
      (e: unknown) => {
        const error = describe(e);
        apply((prev) => ({ data: prev.key === key ? prev.data : undefined, error }));
      },
    );
  }, [key, reloads]);

  const reload = useCallback(() => setReloads((n) => n + 1), []);
  const mutate = useCallback((update: (data: T | undefined) => T | undefined) => {
    setSettled((prev) => ({ ...prev, data: update(prev.data) }));
  }, []);

  return {
    data: settled.data,
    error: settled.error,
    loading: settled.key !== key || settled.reloads !== reloads,
    stale: settled.key !== key,
    reload,
    mutate,
  };
}
