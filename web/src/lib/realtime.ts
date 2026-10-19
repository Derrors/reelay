/**
 * 实时事件总线：优先 SSE，不可用时降级为自适应轮询。
 *
 * - SSE：进入页面先拉一次快照，之后收到 state 事件才重新取数；变化通常 1~2 秒到达。
 * - 降级：EventSource 报错（旧代理不支持流等）时自动切轮询；页面隐藏时降频/暂停，
 *   重新可见立即刷新。
 * - 所有取数逻辑仍走原 REST，事件只负责「通知该刷新了」，不改数据契约。
 * - 同一标签页内所有 Realtime 实例共用一条 EventSource：HTTP/1.1 下浏览器对每个域名
 *   最多保持 6 条连接，每个订阅者各开一条会很快占满，导致普通请求排队挂起。
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

const ALL_SCOPES: ChangeScope[] = ['counts', 'deliveries', 'runtime', 'tasks'];

type StreamEvent =
  | { type: 'hello' }
  | { type: 'state'; changed: ChangeScope[] }
  | { type: 'offline' }
  | { type: 'fatal' };
type StreamListener = (event: StreamEvent) => void;

/** 标签页级共享的 SSE 连接，按订阅者引用计数开关。 */
const stream = {
  es: null as EventSource | null,
  failures: 0,
  listeners: new Set<StreamListener>(),

  subscribe(listener: StreamListener): () => void {
    this.bindPageLifecycle();
    this.listeners.add(listener);
    if (!this.es) this.open();
    else if (this.es.readyState === EventSource.OPEN) listener({ type: 'hello' }); // 已连通：新订阅者立即对齐
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
    this.failures = 0;
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
    // ping 不触发取数，仅用于保活。EventSource 自己会重连；连续失败说明流被拦，通知订阅者降级轮询。
    es.onerror = () => {
      this.failures += 1;
      if (this.failures >= 2) {
        // 降级后的订阅者改为各自轮询，不再挂在这条连接上
        const dropped = [...this.listeners];
        this.listeners.clear();
        this.close();
        for (const listener of dropped) listener({ type: 'fatal' });
      } else {
        this.emit({ type: 'offline' });
      }
    };
  },
};

export class Realtime {
  private opts: Required<Pick<RealtimeOptions, 'scopes'>> & RealtimeOptions;
  private unsubscribe: (() => void) | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private mode: 'live' | 'polling' | 'offline' = 'offline';
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
    this.unsubscribe?.();
    this.unsubscribe = null;
    if (this.pollTimer) clearTimeout(this.pollTimer);
  }

  /** 操作后立即对齐一次（批准/重试/保存等场景调用）。 */
  flush = () => {
    this.fire(ALL_SCOPES);
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
    this.unsubscribe = stream.subscribe((event) => {
      if (event.type === 'hello') {
        this.setMode('live');
        this.fire(ALL_SCOPES);
      } else if (event.type === 'state') {
        this.fire(event.changed);
      } else if (event.type === 'offline') {
        this.setMode('offline');
      } else {
        this.unsubscribe = null;
        this.startPolling();
      }
    });
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
      if (this.mode === 'polling') this.schedulePoll(HIDDEN_POLL_MS);
    } else {
      // 回到前台立刻对齐。
      this.fire(ALL_SCOPES);
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
