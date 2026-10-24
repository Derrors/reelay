import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Realtime } from '@/lib/realtime';

class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  closed = false;
  onerror: (() => void) | null = null;
  private handlers = new Map<string, Array<(ev: MessageEvent) => void>>();
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, handler: (ev: MessageEvent) => void) {
    this.handlers.set(type, [...(this.handlers.get(type) ?? []), handler]);
  }
  emit(type: string, data = '{}') {
    if (type === 'hello') this.readyState = FakeEventSource.OPEN;
    for (const handler of this.handlers.get(type) ?? []) handler({ data } as MessageEvent);
  }
  /** 浏览器自己会重连的错误（网络中断）。 */
  drop() {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.();
  }
  /** 非 200 响应：浏览器关闭连接、不再重试。 */
  reject() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.();
  }
  close() { this.closed = true; this.readyState = FakeEventSource.CLOSED; }
}

const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];
const started: Realtime[] = [];
function start(options: ConstructorParameters<typeof Realtime>[0]) {
  const realtime = new Realtime(options);
  realtime.start();
  started.push(realtime);
  return realtime;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn(), hidden: false, hasFocus: () => true });
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
afterEach(() => {
  // 共享连接是模块级的：每个用例结束时全部停掉，连接随之关闭、计数清零
  started.splice(0).forEach((r) => r.stop());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Realtime 共享连接', () => {
  it('同一标签页的多个订阅者只开一条 SSE，并按范围分发事件', () => {
    const counts = vi.fn();
    const tasks = vi.fn();
    const a = start({ scopes: ['counts'], onChange: counts });
    const b = start({ scopes: ['tasks'], onChange: tasks });
    expect(FakeEventSource.instances).toHaveLength(1);
    latest().emit('state', JSON.stringify({ changed: ['tasks'] }));
    expect(tasks).toHaveBeenCalledTimes(1);
    expect(counts).not.toHaveBeenCalled();
    a.stop();
    expect(latest().closed).toBe(false);
    b.stop();
    expect(latest().closed).toBe(true);
  });

  it('连接已建立后加入的订阅者立即对齐一次', () => {
    start({ onChange: vi.fn() });
    latest().emit('hello');
    const late = vi.fn();
    start({ onChange: late });
    expect(late).toHaveBeenCalledTimes(1);
  });
});

describe('Realtime 断线重连', () => {
  it('接口返回非 200 时丢掉关闭的连接，按退避重新打开，连上后回到实时', () => {
    const modes: string[] = [];
    const onChange = vi.fn();
    start({ onChange, onMode: (m) => modes.push(m) });
    const first = latest();
    first.emit('hello');
    expect(modes).toEqual(['live']);

    first.reject();
    expect(first.closed).toBe(true);
    expect(modes.at(-1)).toBe('offline');
    expect(FakeEventSource.instances).toHaveLength(1);

    vi.advanceTimersByTime(1000);
    expect(FakeEventSource.instances).toHaveLength(2);
    onChange.mockClear();
    latest().emit('hello');
    expect(modes.at(-1)).toBe('live');
    expect(onChange).toHaveBeenCalledTimes(1);  // 重连后立即对齐
  });

  it('浏览器自己会重连的错误不另开连接', () => {
    start({ onChange: vi.fn() });
    latest().drop();
    vi.advanceTimersByTime(60000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('退避间隔逐次翻倍，最长 30 秒', () => {
    start({ onChange: vi.fn() });
    const delays = [1000, 2000, 4000, 8000, 16000, 30000, 30000];
    for (const delay of delays) {
      const count = FakeEventSource.instances.length;
      latest().reject();
      vi.advanceTimersByTime(delay - 1);
      expect(FakeEventSource.instances).toHaveLength(count);
      vi.advanceTimersByTime(1);
      expect(FakeEventSource.instances).toHaveLength(count + 1);
    }
  });

  it('全部订阅者退出后不再重连', () => {
    const r = start({ onChange: vi.fn() });
    latest().reject();
    r.stop();
    vi.advanceTimersByTime(60000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

describe('Realtime 降级与恢复', () => {
  it('连续失败后改为轮询，SSE 恢复后停止轮询回到实时', () => {
    const modes: string[] = [];
    const onChange = vi.fn();
    start({ onChange, onMode: (m) => modes.push(m) });
    latest().reject();
    vi.advanceTimersByTime(1000);
    latest().reject();
    expect(modes.at(-1)).toBe('polling');

    vi.advanceTimersByTime(0);
    expect(onChange).toHaveBeenCalledTimes(1);  // 降级时立即取一次
    vi.advanceTimersByTime(1999);
    expect(onChange).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);  // 第 2 次失败后 2 秒重试 SSE；轮询 3 秒一次还没到
    expect(FakeEventSource.instances).toHaveLength(3);
    vi.advanceTimersByTime(1000);
    expect(onChange).toHaveBeenCalledTimes(2);  // 轮询照常进行

    latest().emit('hello');
    expect(modes.at(-1)).toBe('live');
    onChange.mockClear();
    vi.advanceTimersByTime(60000);
    expect(onChange).not.toHaveBeenCalled();  // 回到实时后不再轮询
  });

  it('降级期间加入的订阅者直接轮询，恢复时一起回到实时', () => {
    start({ onChange: vi.fn() });
    latest().reject();
    vi.advanceTimersByTime(1000);
    latest().reject();
    const modes: string[] = [];
    const late = vi.fn();
    start({ onChange: late, onMode: (m) => modes.push(m) });
    expect(modes).toEqual(['polling']);
    vi.advanceTimersByTime(0);
    expect(late).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    latest().emit('hello');
    expect(modes.at(-1)).toBe('live');
  });
});
