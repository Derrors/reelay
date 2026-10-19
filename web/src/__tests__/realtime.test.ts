import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Realtime } from '@/lib/realtime';

class FakeEventSource {
  static OPEN = 1;
  static instances: FakeEventSource[] = [];
  readyState = 0;
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
  close() { this.closed = true; }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn(), hidden: false, hasFocus: () => true });
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
afterEach(() => vi.unstubAllGlobals());

describe('Realtime 共享连接', () => {
  it('同一标签页的多个订阅者只开一条 SSE，并按范围分发事件', () => {
    const counts = vi.fn();
    const tasks = vi.fn();
    const a = new Realtime({ scopes: ['counts'], onChange: counts });
    const b = new Realtime({ scopes: ['tasks'], onChange: tasks });
    a.start();
    b.start();
    expect(FakeEventSource.instances).toHaveLength(1);
    FakeEventSource.instances[0].emit('state', JSON.stringify({ changed: ['tasks'] }));
    expect(tasks).toHaveBeenCalledTimes(1);
    expect(counts).not.toHaveBeenCalled();
    a.stop();
    expect(FakeEventSource.instances[0].closed).toBe(false);
    b.stop();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it('连接已建立后加入的订阅者立即对齐一次', () => {
    const first = new Realtime({ onChange: vi.fn() });
    first.start();
    FakeEventSource.instances[0].emit('hello');
    const late = vi.fn();
    const second = new Realtime({ onChange: late });
    second.start();
    expect(late).toHaveBeenCalledTimes(1);
    first.stop();
    second.stop();
  });
});
