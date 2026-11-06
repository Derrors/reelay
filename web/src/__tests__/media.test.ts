import { describe, expect, it } from 'vitest';
import { dayLabel, groupByDay } from '@/lib/dayGroups';
import { toneFromPixels } from '@/lib/posterTone';

const px = (...rgba: number[][]) => rgba.flat();

describe('海报主色调', () => {
  it('取主体色的色相，饱和度封顶', () => {
    const red = toneFromPixels(px([220, 30, 30, 255], [200, 20, 20, 255]));
    expect(red?.hue).toBe(0);
    expect(red?.saturation).toBe(55);
    const blue = toneFromPixels(px([30, 60, 220, 255]));
    expect(blue?.hue).toBeGreaterThan(220);
    expect(blue?.hue).toBeLessThan(235);
  });

  it('大面积黑边不盖过主体色，透明像素不算', () => {
    const tone = toneFromPixels(px(...Array(20).fill([0, 0, 0, 255]), [40, 180, 60, 255], [255, 0, 0, 0]));
    expect(tone?.hue).toBeGreaterThan(110);
    expect(tone?.hue).toBeLessThan(140);
    expect(toneFromPixels(px([10, 10, 10, 0]))).toBeNull();
  });
});

describe('按日期分组', () => {
  const now = new Date(2026, 10, 6, 20, 0).getTime();
  const at = (d: number, h = 12) => new Date(2026, 10, d, h).getTime() / 1000;

  it('今天、昨天、本周带星期，更早只写日期，跨年写年份', () => {
    expect(dayLabel(at(6, 0), now)).toBe('今天');
    expect(dayLabel(at(5, 23), now)).toBe('昨天');
    expect(dayLabel(at(3), now)).toMatch(/^11月3日 周/);
    expect(dayLabel(at(1, 12) - 86400 * 10, now)).toBe('10月22日');
    expect(dayLabel(new Date(2025, 11, 31).getTime() / 1000, now)).toBe('2025年12月31日');
  });

  it('保持原顺序，相邻同一天的合成一组', () => {
    const items = [at(6, 15), at(6, 9), at(5), at(3)];
    expect(groupByDay(items, (t) => t, now).map((g) => [g.label.slice(0, 5), g.items.length]))
      .toEqual([['今天', 2], ['昨天', 1], ['11月3日', 1]]);
  });
});
