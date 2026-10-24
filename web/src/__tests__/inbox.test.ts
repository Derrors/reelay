import { describe, expect, it } from 'vitest';
import type { IInboxResp } from '@/api/inbox';
import type { IInboxItem } from '@/lib/types';
import { actionBody, ACTION_ENDPOINT } from '@/lib/taskActions';
import {
  adviceBatches, adviceKey, advicePending, markSubscribed, shareBadge, visibleItems, withoutTasks, type InboxData,
} from '@/pages/InboxPage/inbox';

const item = (id: string, patch: Partial<IInboxItem> = {}): IInboxItem => ({
  task_id: id, state: 'pending_approval', source: '@bot', created_at: 100, updated_at: 100, error: null,
  title: `片名${id}`, year: null, media_type: 'tv', season: 1, episodes: [1], resolution: null, size: null, reason: null,
  confidence: 0.9, confidence_caps: [], fallback: false, tmdb_matched: true, poster_path: null, save_dir_alias: null,
  auto_dir: null, auto_path: null, episode_pairs: null, share: null, preview: '', storage_phase: null, has_landed: false,
  organize_error: null, organize_conflict: null, transfer_partial: null, actions: ['approve', 'reject', 'subscribe'],
  ...patch,
});
const resp = (items: IInboxItem[]): IInboxResp => ({ items, save_dirs: [], save_dir_paths: {} });

describe('待审批列表的筛选与排序', () => {
  const items = [
    item('a', { created_at: 3, confidence: 0.5, title: '漫长的季节' }),
    item('b', { created_at: 1, confidence: 0.2, source: '@movies' }),
    item('c', { created_at: 2, confidence: 0.9 }),
  ];
  it('默认最新在前，可按最早、按置信度从低到高', () => {
    expect(visibleItems(items, '', 'newest').map((i) => i.task_id)).toEqual(['a', 'c', 'b']);
    expect(visibleItems(items, '', 'oldest').map((i) => i.task_id)).toEqual(['b', 'c', 'a']);
    expect(visibleItems(items, '', 'confidence').map((i) => i.task_id)).toEqual(['b', 'a', 'c']);
  });
  it('按片名或来源筛选，不改动原数组', () => {
    expect(visibleItems(items, ' 季节 ', 'newest').map((i) => i.task_id)).toEqual(['a']);
    expect(visibleItems(items, 'MOVIES', 'newest').map((i) => i.task_id)).toEqual(['b']);
    expect(items.map((i) => i.task_id)).toEqual(['a', 'b', 'c']);
  });
});

describe('读取分享的提示', () => {
  const share = (status: NonNullable<IInboxItem['share']>['status'], note = '', fromShare = false) =>
    ({ status, note, videos: 3, from_share: fromShare });
  it('集数来自分享时注明，消息里写了集数的不打扰', () => {
    expect(shareBadge(share('ok', '', true))).toMatchObject({ tone: 'info', label: '集数来自分享' });
    expect(shareBadge(share('ok'))).toBeNull();
    expect(shareBadge(null)).toBeNull();
  });
  it('转存时会转人工的情况提前标出，读取出错不提示', () => {
    expect(shareBadge(share('unavailable'))).toMatchObject({ tone: 'danger' });
    expect(shareBadge(share('issue', '含多季：S01、S02'))).toMatchObject({ tone: 'warning', label: '含多季' });
    expect(shareBadge(share('error'))).toBeNull();
  });
});

describe('按建议处理与本地更新', () => {
  const data: InboxData = {
    pending_approval: resp([
      item('a', { advice: { action: 'approve', label: '', text: '', source: 'rule' } }),
      item('b', { advice: { action: 'reject', label: '', text: '', source: 'rule' } }),
      item('c', { advice: { action: 'review', label: '', text: '', source: 'ai' } }),
    ]),
    need_manual: resp([item('m', { state: 'need_manual', actions: ['dismiss'] })]),
  };

  it('所选条目按建议分成批准与忽略，需要人看的留下', () => {
    expect(adviceBatches(data.pending_approval.items, new Set(['a', 'b', 'c']))).toEqual({ approve: ['a'], reject: ['b'] });
    expect(adviceBatches(data.pending_approval.items, new Set(['c']))).toEqual({ approve: [], reject: [] });
  });

  it('移除操作成功的任务时两个列表都处理，不依赖当前页签', () => {
    const next = withoutTasks(data, ['a', 'm']);
    expect(next.pending_approval.items.map((i) => i.task_id)).toEqual(['b', 'c']);
    expect(next.need_manual.items).toEqual([]);
    expect(data.pending_approval.items).toHaveLength(3);
  });

  it('订阅后同一部剧标为已订阅，不再提供订阅操作', () => {
    const next = markSubscribed(data, ['a']);
    expect(next.pending_approval.items[0]).toMatchObject({ subscribed: true, actions: ['approve', 'reject'] });
    expect(next.pending_approval.items[1].subscribed).toBeUndefined();
  });

  it('只为规则写的、还没请求过的建议请求 AI 措辞', () => {
    const requested = new Set([adviceKey(data.pending_approval.items[0])]);
    expect(advicePending(data.pending_approval.items, requested).map((i) => i.task_id)).toEqual(['b']);
  });
});

describe('任务操作的接口与请求体', () => {
  it('忽略失败任务与处理冲突沿用已有的接口', () => {
    expect(ACTION_ENDPOINT.dismiss).toBe('reject');
    expect(ACTION_ENDPOINT.resolve_conflict).toBe('reorganize');
    expect(ACTION_ENDPOINT.accept_partial).toBe('accept-partial');
  });
  it('确认与重新整理带上保存目录，处理冲突带上选择', () => {
    expect(actionBody('approve', { dir: '剧集' })).toEqual({ save_dir_alias: '剧集' });
    expect(actionBody('reorganize')).toEqual({ save_dir_alias: null });
    expect(actionBody('resolve_conflict', { conflict: 'replace', dir: '剧集' })).toEqual({ conflict: 'replace' });
    expect(actionBody('retry')).toBeUndefined();
  });
});
