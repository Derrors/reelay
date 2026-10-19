/**
 * 实时事件总线：优先 SSE，不可用时降级为自适应轮询。
 *
 * - SSE：进入页面先拉一次快照，之后收到 state 事件才重新取数；变化通常 1~2 秒到达。
 * - 降级：EventSource 报错（旧代理不支持流等）时自动切轮询；页面隐藏时降频/暂停，
 *   重新可见立即刷新。
 * - 所有取数逻辑仍走原 REST，事件只负责「通知该刷新了」，不改数据契约。
 */

import { apiUrl } from '@/api/client';

export type ChangeScope = 'counts' | 'deliveries' | 'runtime' | 'tasks';

export interface RealtimeOptions {
  /** 需要监听的变化范围；命中任意一项即回调。 */
  scopes?: ChangeScope[];
  /** 收到变化 / 重连对齐 / 手动刷新时触发。 */
  onChange: (scopes: ChangeScope[]) => void;
  /** 连接模式变化，可用于展示「实时/轮询/离线」角标。 */
  onMode?: (mode: 'live' | 'polling' | 'offline') => void;
}

const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 15000;
const HIDDEN_POLL_MS = 30000;

function intersects(a: ChangeScope[], b?: ChangeScope[]): boolean {
  if (!b || b.length === 0) return true;
  return a.some((s) => b.includes(s));
}

export class Realtime {
  private opts: Required<Pick<RealtimeOptions, 'scopes'>> & RealtimeOptions;
  private es: EventSource | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private mode: 'live' | 'polling' | 'offline' = 'offline';
  private failures = 0;
  private stopped = false;

  constructor(opts: RealtimeOptions) {
    this.opts = { scopes: [], ...opts };
  }

  start() {
    this.stopped = false;
    document.addEventListener('visibilitychange', this.onVisibility);
    window.addEventListener('online', this.flush);
    window.addEventListener('offline', this.setOffline);
    this.openSse();
  }

  stop() {
    this.stopped = true;
    document.removeEventListener('visibilitychange', this.onVisibility);
    window.removeEventListener('online', this.flush);
    window.removeEventListener('offline', this.setOffline);
    this.es?.close();
    this.es = null;
    if (this.pollTimer) clearTimeout(this.pollTimer);
  }

  /** 操作后立即对齐一次（批准/重试/保存等场景调用）。 */
  flush = () => {
    this.fire(['counts', 'deliveries', 'runtime', 'tasks']);
    // live 模式下不必额外做什么，观察者下一拍自然会推；poll 模式立即取一次。
    if (this.mode === 'polling') this.schedulePoll(0);
  };

  // ------------------------------------------------------------------ //
  // SSE
  // ------------------------------------------------------------------ //
  private openSse() {
    if (this.stopped || typeof EventSource === 'undefined') {
      this.startPolling();
      return;
    }
    const es = new EventSource(apiUrl('events'));
    this.es = es;

    es.addEventListener('hello', () => {
      // 新连接/重连后立刻对齐，弥补断线期间漏掉的变化。
      this.failures = 0;
      this.setMode('live');
      this.fire(['counts', 'deliveries', 'runtime', 'tasks']);
    });

    es.addEventListener('state', (ev: MessageEvent) => {
      try {
        const payload = JSON.parse(ev.data || '{}');
        const changed = Array.isArray(payload.changed) ? payload.changed : [];
        if (intersects(changed, this.opts.scopes)) {
          this.fire(changed);
        }
      } catch {
        this.fire(['counts', 'deliveries', 'runtime', 'tasks']);
      }
    });

    // ping 不触发取数，仅用于保活；浏览器原生会自动重连。
    es.onerror = () => {
      this.failures += 1;
      // EventSource 自己会重连；连续失败较久说明流被拦，降级轮询。
      if (this.failures >= 2) {
        es.close();
        this.es = null;
        this.startPolling();
      } else {
        this.setMode('offline');
      }
    };
  }

  // ------------------------------------------------------------------ //
  // 自适应轮询（降级路径）
  // ------------------------------------------------------------------ //
  private startPolling() {
    if (this.stopped) return;
    this.setMode('polling');
    this.schedulePoll(0);
  }

  private schedulePoll(delay: number) {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.stopped) return;
    this.pollTimer = setTimeout(() => {
      this.fire(['counts', 'deliveries', 'runtime', 'tasks']);
      this.schedulePoll(this.nextDelay());
    }, delay);
  }

  private nextDelay(): number {
    if (document.hidden) return HIDDEN_POLL_MS;
    // 简单自适应：近期待命时放慢，活跃时保持 3s。
    return document.hasFocus() ? ACTIVE_POLL_MS : IDLE_POLL_MS;
  }

  private onVisibility = () => {
    if (document.hidden) {
      if (this.mode === 'polling') this.schedulePoll(HIDDEN_POLL_MS);
    } else {
      // 回到前台立刻对齐。
      this.fire(['counts', 'deliveries', 'runtime', 'tasks']);
      if (this.mode === 'polling') this.schedulePoll(0);
    }
  };

  private setOffline = () => this.setMode('offline');

  private setMode(mode: 'live' | 'polling' | 'offline') {
    if (this.mode === mode) return;
    this.mode = mode;
    this.opts.onMode?.(mode);
  }

  private fire(scopes: ChangeScope[]) {
    if (intersects(scopes, this.opts.scopes)) {
      this.opts.onChange(scopes);
    }
  }
}
