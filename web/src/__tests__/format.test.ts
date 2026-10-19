import { describe, expect, it } from 'vitest';
import { episodeLabel, seasonsLabel } from '@/lib/format';

describe('季集标签', () => {
  it('跨多季的合集显示季范围与总集数', () => {
    const pairs = [[1, 1], [1, 2], [2, 1], [2, 2], [3, 1]];
    expect(seasonsLabel(pairs)).toBe('S01–S03 · 5 集');
    expect(seasonsLabel([[1, 1], [3, 1]])).toBe('S01、S03 · 2 集');
    expect(episodeLabel(null, [1, 2], pairs)).toBe('S01–S03 · 5 集');
  });

  it('单季沿用季与集', () => {
    expect(episodeLabel(1, [5, 6], [[1, 5], [1, 6]])).toBe('S01 · E05–E06');
    expect(episodeLabel(1, [5, 6], null)).toBe('S01 · E05–E06');
    expect(episodeLabel(null, [])).toBe('');
  });
});
