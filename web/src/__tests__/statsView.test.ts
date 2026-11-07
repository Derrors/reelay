import { describe, expect, it } from 'vitest';
import { yTicks } from '@/pages/StatsPage/statsView';

describe('yTicks', () => {
  it('任务数是整数：步长至少为 1', () => {
    expect(yTicks(0)).toEqual([0, 1]);
    expect(yTicks(1)).toEqual([0, 1]);
    expect(yTicks(3)).toEqual([0, 1, 2, 3]);
  });

  it('步长取 1/2/5 × 10ⁿ，顶部不低于最大值', () => {
    expect(yTicks(19)).toEqual([0, 5, 10, 15, 20]);
    expect(yTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(yTicks(100)).toEqual([0, 50, 100]);
    expect(yTicks(1234)).toEqual([0, 500, 1000, 1500]);
  });
});
