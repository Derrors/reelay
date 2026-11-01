import { describe, expect, it } from 'vitest';
import { equal, getPath, obj, prune, rows, setPath } from '@/lib/values';
import {
  booleanValue, clearSecrets, CONFIG_SECTIONS, describeConfigError, dirNamesFrom, normalizeUrl, pathLabel,
} from '@/pages/ConfigPage/shared';

describe('配置草稿的读写', () => {
  it('setPath 只复制路径上的对象，不改动原配置', () => {
    const saved = { plugins: { organizer: { layout: 'none', naming: { tv: 'x' } }, tracker: { enabled: true } } };
    const next = setPath(saved, 'plugins.organizer.enabled', false);
    expect(getPath(next, 'plugins.organizer')).toEqual({ layout: 'none', naming: { tv: 'x' }, enabled: false });
    expect(getPath(next, 'plugins.tracker')).toBe(saved.plugins.tracker);
    expect(getPath(saved, 'plugins.organizer.enabled')).toBeUndefined();
  });
});

describe('仅处理订阅的配置草稿', () => {
  it('布尔选项区分未设置与显式关闭', () => {
    expect(booleanValue(undefined)).toBe('');
    expect(booleanValue(null)).toBe('');
    expect(booleanValue(true)).toBe('on');
    expect(booleanValue(false)).toBe('off');
  });
  it.each([true, false])('全局设置 %s 写回 defaults，保留其他默认规则', (enabled) => {
    const saved = { defaults: { approval_mode: 'manual', subscriptions_only: !enabled } };
    const draft = setPath(saved, 'defaults.subscriptions_only', enabled);
    const reloaded = obj(JSON.parse(JSON.stringify(draft)));
    expect(reloaded.defaults).toEqual({ approval_mode: 'manual', subscriptions_only: enabled });
    expect(saved.defaults.subscriptions_only).toBe(!enabled);
    expect(CONFIG_SECTIONS.filter(s => s.paths.some(p => !equal(getPath(saved, p), getPath(draft, p))))
      .map(s => ({ label: s.label, tab: s.tab }))).toEqual([{ label: '默认处理方式', tab: 'common' }]);
  });
  it.each([true, false])('来源覆盖 %s 在清理和序列化后保留，不变成继承', (enabled) => {
    const source = { bot: '@resource_bot', approval_mode: 'manual' };
    const draft = {
      defaults: { subscriptions_only: !enabled },
      sources: [prune({ ...source, subscriptions_only: enabled })],
    };
    const reloaded = obj(JSON.parse(JSON.stringify(draft)));
    expect(rows(reloaded.sources)[0]).toEqual({ ...source, subscriptions_only: enabled });
    expect(booleanValue(rows(reloaded.sources)[0].subscriptions_only)).toBe(enabled ? 'on' : 'off');
    expect(obj(reloaded.defaults).subscriptions_only).toBe(!enabled);
  });
  it('恢复默认只删除来源的覆盖项，归入监听来源分区', () => {
    const saved = {
      defaults: { subscriptions_only: true },
      sources: [{ bot: '@resource_bot', subscriptions_only: false }],
    };
    const draft = { ...saved, sources: [prune({ ...saved.sources[0], subscriptions_only: undefined })] };
    const reloaded = obj(JSON.parse(JSON.stringify(draft)));
    expect(rows(reloaded.sources)[0]).toEqual({ bot: '@resource_bot' });
    expect(booleanValue(rows(reloaded.sources)[0].subscriptions_only)).toBe('');
    expect(obj(reloaded.defaults).subscriptions_only).toBe(true);
    expect(saved.sources[0].subscriptions_only).toBe(false);
    expect(CONFIG_SECTIONS.filter(s => s.paths.some(p => !equal(getPath(saved, p), getPath(draft, p))))
      .map(s => ({ label: s.label, tab: s.tab }))).toEqual([{ label: '监听来源', tab: 'sources' }]);
  });
});

describe('后端校验错误归到分区', () => {
  it('按配置块标上分区名', () => {
    expect(describeConfigError('llm.model 不能为空')).toBe('「AI 服务」llm.model 不能为空');
    expect(describeConfigError('sources[1] bot 重复')).toBe('「监听来源」sources[1] bot 重复');
    expect(describeConfigError('approval.timeout_hours 必须大于零')).toBe('「确认超时」approval.timeout_hours 必须大于零');
  });
  it('defaults 下的字段按具体字段找分区，找不到时原样显示', () => {
    expect(describeConfigError('defaults.confidence_threshold 必须在 0 到 1 之间')).toBe('「识别准确度」defaults.confidence_threshold 必须在 0 到 1 之间');
    expect(describeConfigError('defaults.save_to 引用了不存在的目录')).toBe('「默认处理方式」defaults.save_to 引用了不存在的目录');
    expect(describeConfigError('defaults.subscriptions_only 必须是布尔值')).toBe('「默认处理方式」defaults.subscriptions_only 必须是布尔值');
    expect(describeConfigError('sources[0].subscriptions_only 必须是布尔值')).toBe('「监听来源」sources[0].subscriptions_only 必须是布尔值');
    expect(describeConfigError('defaults.parser 必须是 Provider 名称')).toBe('defaults.parser 必须是 Provider 名称');
    expect(describeConfigError('开启转发时必须提供 target 与 template')).toBe('开启转发时必须提供 target 与 template');
  });
});

describe('接口地址与密钥', () => {
  it('比较地址时忽略首尾空白与末尾斜杠', () => {
    expect(normalizeUrl(' https://api.example.com/v1/ ')).toBe('https://api.example.com/v1');
    expect(normalizeUrl(undefined)).toBe('');
  });
  it('后端清除的密钥标为未设置，其他不变', () => {
    const status = { llm_api_key: { label: 'AI 接口密钥', set: true }, tmdb_api_key: { label: 'TMDB 密钥', set: true } };
    expect(clearSecrets(status, ['llm_api_key'])).toEqual({
      llm_api_key: { label: 'AI 接口密钥', set: false }, tmdb_api_key: { label: 'TMDB 密钥', set: true },
    });
    expect(clearSecrets(status, [])).toBe(status);
  });
});

describe('保存目录的显示名', () => {
  it('按读取时的配置把网盘路径对应到目录编号', () => {
    const config = { save_dirs: { 剧集: '101', 电影: '102', 根: '0' } };
    expect(dirNamesFrom(config, { 剧集: '/影视/剧集', 根: '/' })).toEqual({ '0': '根目录', '101': '影视 / 剧集' });
    expect(pathLabel('/')).toBe('');
  });
});
