import { useSyncExternalStore } from 'react';
import { dashboardApi } from '@/api/dashboard';
import { Realtime, type RealtimeMode } from '@/lib/realtime';
import type { IDashboard } from '@/lib/types';
import { errorMessage } from '@/lib/utils';

interface DashboardState {
  dashboard: IDashboard | null;
  error: string;
  /** 实时通道的连接方式（实时 / 轮询 / 重连中）。 */
  mode: RealtimeMode;
}

/**
 * 侧边栏徽标、总览与任务页的计数共用一份 /api/dashboard：整个标签页只订阅一次实时通道，
 * 同一时刻最多一个请求；请求期间又有变化时，结束后再补取一次。
 */
let state: DashboardState = { dashboard: null, error: '', mode: 'offline' };
const listeners = new Set<() => void>();
let realtime: Realtime | null = null;
let inflight: Promise<void> | null = null;
let queued = false;

function update(patch: Partial<DashboardState>) {
  state = { ...state, ...patch };
  for (const listener of [...listeners]) listener();
}

/** 重新读取运行状态；返回本次（含补取）完成的 Promise。 */
export function refreshDashboard(): Promise<void> {
  if (inflight) {
    queued = true;
    return inflight;
  }
  inflight = dashboardApi.get()
    .then((dashboard) => update({ dashboard, error: '' }))
    .catch((e: unknown) => update({ error: errorMessage(e, '读取运行状态失败') }))
    .finally(() => {
      inflight = null;
      if (queued && listeners.size) {
        queued = false;
        void refreshDashboard();
      }
    });
  return inflight;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!realtime) {
    realtime = new Realtime({
      scopes: ['counts', 'deliveries', 'runtime'],
      onChange: () => { void refreshDashboard(); },
      onMode: (mode) => update({ mode }),
    });
    realtime.start();
    void refreshDashboard();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      realtime?.stop();
      realtime = null;
      queued = false;
      state = { ...state, mode: 'offline' };
    }
  };
}

const getSnapshot = () => state;

export function useDashboard(): DashboardState {
  return useSyncExternalStore(subscribe, getSnapshot);
}
