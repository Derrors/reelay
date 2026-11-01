import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { resourceLinkMeta, STATE_META } from '@/lib/status';

describe('识别测试的资源链接提示', () => {
  const ed2k = [{
    name: '示例.S01E31.2160p.mkv', size: 366323694, hash: '0123456789abcdef0123456789abcdef',
    url: 'ed2k://|file|示例.S01E31.2160p.mkv|366323694|0123456789abcdef0123456789abcdef|/',
  }];

  it('只有电驴链接时显示已提取，不误报缺少 115 链接', () => {
    expect(resourceLinkMeta({ share_code: null, ed2k })).toEqual({ label: '已提取电驴链接', tone: 'success' });
  });

  it('115 分享仍显示已提取 115 链接', () => {
    expect(resourceLinkMeta({ share_code: 'swabc123', ed2k: [] }))
      .toEqual({ label: '已提取 115 链接', tone: 'success' });
  });

  it('两种链接都有时，与后端一致优先使用 115 分享', () => {
    expect(resourceLinkMeta({ share_code: 'swabc123', ed2k }))
      .toEqual({ label: '已提取 115 链接', tone: 'success' });
  });

  it.each([undefined, null, {}, { share_code: null, ed2k: [] }])('没有提取到链接时使用通用提示：%j', (parsed) => {
    expect(resourceLinkMeta(parsed)).toEqual({ label: '没有找到资源链接', tone: 'warning' });
  });
});

describe('任务状态文案与后端状态机保持一致', () => {
  it('后端 TaskState 的每个取值都有界面文案', () => {
    // 直接读取后端契约，后端新增状态而前端遗漏时这里会失败。
    const contracts = readFileSync(resolve(import.meta.dirname, '../../../reelay/core/contracts.py'), 'utf8');
    const block = contracts.slice(contracts.indexOf('class TaskState'), contracts.indexOf('class ApprovalMode'));
    const backendStates = [...block.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((match) => match[1]);
    expect(backendStates.length).toBeGreaterThan(10);
    expect(Object.keys(STATE_META).sort()).toEqual(backendStates.sort());
  });
});
