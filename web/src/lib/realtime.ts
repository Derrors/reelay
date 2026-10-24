/**
 * 实时事件总线：优先 SSE，不可用时降级为自适应轮询，SSE 恢复后自动回到实时。
 *
 * - SSE：进入页面先拉一次快照，之后收到 state 事件才重新取数；变化通常 1~2 秒到达。
 * - 断线：网络断开时浏览器会自己重连；但接口返回非 200（重启时反代的 502、会话失效的 401）时
 *   浏览器直接关闭连接、不再重试，这里丢掉这条连接并按退避重新打开。
 * - 降级：连续失败时订阅者改为轮询（页面隐藏时降频），同时继续按退避尝试 SSE，连上后停止轮询。
 * - 所有取数逻辑仍走原 REST，事件只负责「通知该刷新了」，不改数据契约。
 * - 同一标签页内所有 Realtime 实例共用一条 EventSource：HTTP/1.1 下浏览器对每个域名
 *   最多保持 6 条连接，每个订阅者各开一条会很快占满，导致普通请求排队挂起。
 */

import { apiUrl } from '@/api/request';

export type ChangeScope = 'counts' | 'deliveries' | 'runtime' | 'tasks';
export type RealtimeMode = 'live' | 'polling' | 'offline';

export interface RealtimeOptions {
  /** 需要监听的变化范围；命中任意一项即回调。 */
  scopes?: ChangeScope[];
  /** 收到变化 / 重连对齐 / 手动刷新时触发。 */
  onChange: (scopes: ChangeScope[]) => void;
  /** 连接模式变化，可用于展示「实时/轮询/离线」角标。 */
  onMode?: (mode: RealtimeMode) => void;
}

const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 15000;
const HIDDEN_POLL_MS = 30000;
/** 连续失败几次后改为轮询。 */
const FALLBACK_AFTER = 2;
/** 重新打开 SSE 的退避：1 秒起，每次翻倍，最长 30 秒（轮询期间也按这个间隔尝试恢复）。 */
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 30000;

function intersects(a: ChangeScope[], b?: ChangeScope[]): boolean {
  if (!b || b.length === 0) return true;
  return a.some((s) => b.includes(s));
}

const ALL_SCOPES: ChangeScope[] = ['counts', 'deliveries', 'runtime', 'tasks'];

type StreamEvent =
  | { type: 'hello' }
  | { type: 'state'; changed: ChangeScope[] }
  | { type: 'offline' }
  /** 连续失败：先轮询着，SSE 恢复后会再收到 hello。 */
  | { type: 'degraded' };
type StreamListener = (event: StreamEvent) => void;

/** 标签页级共享的 SSE 连接，按订阅者引用计数开关。 */
const stream = {
  es: null as EventSource | null,
  failures: 0,
  retryTimer: null as ReturnType<typeof setTimeout> | null,
  listeners: new Set<StreamListener>(),

  subscribe(listener: StreamListener): () => void {
    this.bindPageLifecycle();
    this.listeners.add(listener);
    if (this.es?.readyState === EventSource.OPEN) listener({ type: 'hello' }); // 已连通：新订阅者立即对齐
    else if (this.failures >= FALLBACK_AFTER) listener({ type: 'degraded' }); // 正在降级：新订阅者也先轮询
    if (!this.es && !this.retryTimer) this.open();
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) this.close();
    };
  },

  emit(event: StreamEvent) {
    for (const listener of [...this.listeners]) listener(event);
  },

  close() {
    this.es?.close();
    this.es = null;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.failures = 0;
  },

  /** 网络恢复等时机：正在退避等待的话立即重试一次。 */
  retryNow() {
    if (!this.retryTimer || !this.listeners.size) return;
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.open();
  },

  lifecycleBound: false,
  /** 页面进入往返缓存（bfcache）时释放连接，否则后台页面会一直占着连接名额。 */
  bindPageLifecycle() {
    if (this.lifecycleBound || typeof window === 'undefined' || !('onpagehide' in window)) return;
    this.lifecycleBound = true;
    window.addEventListener('pagehide', () => this.close());
    window.addEventListener('pageshow', (event: PageTransitionEvent) => {
      if (event.persisted && this.listeners.size && !this.es) this.open();
    });
  },

  open() {
    const es = new EventSource(apiUrl('events'));
    this.es = es;
    // 新连接/重连后立刻对齐，弥补断线期间漏掉的变化。
    es.addEventListener('hello', () => { this.failures = 0; this.emit({ type: 'hello' }); });
    es.addEventListener('state', (ev: MessageEvent) => {
      let changed = ALL_SCOPES;
      try {
        const payload = JSON.parse(ev.data || '{}');
        if (Array.isArray(payload.changed)) changed = payload.changed;
      } catch { /* 解析失败按全部变化处理 */ }
      this.emit({ type: 'state', changed });
    });
    // ping 不触发取数，仅用于保活。
    es.onerror = () => {
      if (this.es !== es) return;
      this.failures += 1;
      if (es.readyState === EventSource.CLOSED) {
        // 浏览器不会再重连这条：按退避重新打开
        es.close();
        this.es = null;
        const delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** (this.failures - 1));
        this.retryTimer = setTimeout(() => { this.retryTimer = null; this.open(); }, delay);
      }
      this.emit({ type: this.failures >= FALLBACK_AFTER ? 'degraded' : 'offline' });
    };
  },
};

export class Realtime {
  private opts: Required<Pick<RealtimeOptions, 'scopes'>> & RealtimeOptions;
  private unsubscribe: (() => void) | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private polling = false;
  private mode: RealtimeMode = 'offline';
  private stopped = false;

  constructor(opts: RealtimeOptions) {
    this.opts = { scopes: [], ...opts };
  }

  start() {
    this.stopped = false;
    document.addEventListener('visibilitychange', this.onVisibility);
    window.addEventListener('online', this.onOnline);
    window.addEventListener('offline', this.setOffline);
    this.openSse();
  }

  stop() {
    this.stopped = true;
    document.removeEventListener('visibilitychange', this.onVisibility);
    window.removeEventListener('online', this.onOnline);
    window.removeEventListener('offline', this.setOffline);
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.stopPolling();
  }

  /** 操作后立即对齐一次（批准/重试/保存等场景调用）。 */
  flush = () => {
    this.fire(ALL_SCOPES);
    // live 模式下不必额外做什么，观察者下一拍自然会推；轮询时立即取一次。
    if (this.polling) this.schedulePoll(0);
  };

  // ------------------------------------------------------------------ //
  // SSE
  // ------------------------------------------------------------------ //
  private openSse() {
    if (this.stopped) return;
    if (typeof EventSource === 'undefined') {
      this.startPolling();
      return;
    }
    this.unsubscribe = stream.subscribe((event) => {
      if (event.type === 'hello') {
        this.stopPolling();
        this.setMode('live');
        this.fire(ALL_SCOPES);
      } else if (event.type === 'state') {
        this.fire(event.changed);
      } else if (event.type === 'offline') {
        if (!this.polling) this.setMode('offline');
      } else if (!this.polling) {
        this.startPolling();
      }
    });
  }

  // ------------------------------------------------------------------ //
  // 自适应轮询（降级路径）
  // ------------------------------------------------------------------ //
  private startPolling() {
    if (this.stopped) return;
    this.polling = true;
    this.setMode('polling');
    this.schedulePoll(0);
  }

  private stopPolling() {
    this.polling = false;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }

  private schedulePoll(delay: number) {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.stopped || !this.polling) return;
    this.pollTimer = setTimeout(() => {
      this.fire(ALL_SCOPES);
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
      if (this.polling) this.schedulePoll(HIDDEN_POLL_MS);
    } else {
      // 回到前台立刻对齐。
      this.fire(ALL_SCOPES);
      if (this.polling) this.schedulePoll(0);
    }
  };

  private onOnline = () => {
    if (this.polling) this.setMode('polling');
    stream.retryNow();
    this.flush();
  };

  private setOffline = () => this.setMode('offline');

  private setMode(mode: RealtimeMode) {
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
