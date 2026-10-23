import { describe, expect, it } from 'vitest';
import { confirmDialog, subscribeConfirm, type ConfirmRequest } from '@/lib/confirm';

describe('confirmDialog', () => {
  it('resolves with the choice and closes', async () => {
    const seen: Array<ConfirmRequest | null> = [];
    const stop = subscribeConfirm((request) => seen.push(request));
    const answer = confirmDialog({ title: '清空日志？', destructive: true });
    expect(seen.at(-1)?.title).toBe('清空日志？');
    seen.at(-1)?.resolve(true);
    await expect(answer).resolves.toBe(true);
    expect(seen.at(-1)).toBeNull();
    stop();
  });

  it('treats a newer request as cancelling the open one', async () => {
    const first = confirmDialog({ title: '一' });
    const second = confirmDialog({ title: '二' });
    await expect(first).resolves.toBe(false);
    let latest: ConfirmRequest | null = null;
    const stop = subscribeConfirm((request) => { latest = request; });
    expect(latest!.title).toBe('二');
    latest!.resolve(false);
    await expect(second).resolves.toBe(false);
    stop();
  });
});
