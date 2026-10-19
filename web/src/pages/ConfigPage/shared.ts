/** 设置 / 插件页共用的取值工具、校验与中文映射（只在本页使用）。 */
import type { Tone } from '@/lib/status';
import type { IConfigResponse, IExtension, IExtensionCatalog, IExtensionField } from '@/lib/types';

export type Obj = Record<string, unknown>;
export type LoadError = { source: string; error: string };
export type Catalog = IExtensionCatalog;

export const obj = (v: unknown): Obj => v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {};
export const rows = (v: unknown): Obj[] => Array.isArray(v) ? v as Obj[] : [];
export const strings = (v: unknown): string[] => Array.isArray(v) ? v.map(String) : [];
export const text = (v: unknown) => v === undefined || v === null ? '' : String(v);
export const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
/** 去掉值为 undefined 的键：用于「沿用默认」时删除来源上的覆盖项。 */
export const prune = (o: Obj): Obj => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

export const getPath = (root: Obj, path: string) => path.split('.').reduce<unknown>((v, p) => obj(v)[p], root);
export function setPath(root: Obj, path: string, value: unknown): Obj {
  const [first, ...rest] = path.split('.');
  return { ...root, [first]: rest.length ? setPath(obj(root[first]), rest.join('.'), value) : value };
}

export const APPROVAL_OPTIONS = ['manual', 'auto', 'default_pass'];
export const APPROVAL_HINTS: Record<string, string> = {
  manual: '最稳妥：每条资源先推送给你确认，再决定是否转存。',
  auto: '适合信任的来源：自动转存，完成后通知你。',
  default_pass: '完全自动处理，不再发确认或通知。',
};

/** 能力槽位 → 中文名 */
export const SLOT_LABELS: Record<string, string> = {
  source: '消息接入方式', parser: '识别方式', decision: '转存判断方式', storage: '网盘', notify: '通知渠道',
};
export const SLOT_ORDER = ['source', 'parser', 'decision', 'storage'];

/** 能力实现的内部名 → 中文名；未知实现回退到目录里的 label，再回退到内部名。 */
const PROVIDER_LABELS: Record<string, Record<string, string>> = {
  decision: { default: '规则判断', llm: 'AI 按偏好判断' },
  parser: { llm: 'AI 识别（推荐）', regex: '规则识别' },
  source: { telegram: 'Telegram 个人账号', dryrun: '模拟消息（仅演示）' },
  storage: { p115: '115 网盘' },
  notify: { console: '控制 Bot / 日志' },
};
const PROVIDER_HINTS: Record<string, Record<string, string>> = {
  decision: {
    default: '按确认方式与过滤规则决定是否转存，不额外调用 AI。',
    llm: '在规则之外，再让 AI 按你的收藏偏好筛选；不会绕过过滤规则、低置信度与人工确认。',
  },
  parser: {
    llm: '用大模型理解消息，识别片名、季集、清晰度，准确率更高。',
    regex: '按固定规则提取信息，不调用 AI，适合格式规整的来源。',
  },
  source: {
    telegram: '用你自己的 Telegram 账号监听资源 Bot 与频道。',
    dryrun: '内置几条样例消息，用于体验与排查，不连接真实来源。',
  },
  storage: { p115: '转存到你的 115 网盘。' },
  notify: { console: '配置了控制 Bot 时推送确认卡片与通知，否则写入运行日志。' },
};

export function providerLabel(slot: string, name: string, catalog?: Catalog | null): string {
  if (!name) return '';
  const known = PROVIDER_LABELS[slot]?.[name];
  if (known) return known;
  return catalog?.providers[slot]?.find(x => x.name === name)?.label || name;
}

export function providerHint(slot: string, name: string, catalog?: Catalog | null): string {
  return PROVIDER_HINTS[slot]?.[name] ?? catalog?.providers[slot]?.find(x => x.name === name)?.description ?? '';
}

/** 运行时机（hook 阶段 / 事件名）→ 中文 */
export const STAGE_LABELS: Record<string, string> = {
  after_parse: '识别完成后', before_save: '转存前', after_save: '转存完成后',
  on_message: '收到消息时', on_identified: '识别出资源后', on_rejected: '判为广告后',
  on_saved: '入库后', on_failed: '失败时', on_daily: '每日',
};
const STAGE_ORDER = ['after_parse', 'before_save', 'after_save'];
export const stageNames = (slot: string) => slot.split(',').map(s => s.trim()).filter(Boolean)
  .map(s => STAGE_LABELS[s] ?? s);

/** 插件字段可选值 → 中文 */
export const CHOICE_LABELS: Record<string, string> = {
  'zh-CN': '简体中文', 'zh-TW': '繁體中文', 'en-US': 'English',
};

/** 插件字段未写入配置时后端实际使用的值；目录暂不声明默认值，这里仅用于展示。 */
const FIELD_DEFAULTS: Record<string, unknown> = {
  'tmdb.language': 'zh-CN', 'tmdb.timeout': 10,
  'episode_tracker.track_all': true, 'episode_tracker.lookback_days': 30,
};
export const fieldDefault = (f: IExtensionField): unknown =>
  f.default ?? FIELD_DEFAULTS[f.path];

/** 同名扩展（订阅多个事件）合并为一项，运行时机用逗号拼接；兼容未合并的旧后端。 */
export function mergeByName(items: IExtension[]): IExtension[] {
  const merged = new Map<string, IExtension>();
  for (const item of items) {
    const prev = merged.get(item.name);
    if (!prev) { merged.set(item.name, { ...item }); continue; }
    const slots = new Set([...prev.slot.split(','), ...item.slot.split(',')]);
    const paths = new Set(prev.fields.map(f => f.path));
    merged.set(item.name, {
      ...prev,
      slot: [...slots].join(','),
      fields: [...prev.fields, ...item.fields.filter(f => !paths.has(f.path))],
    });
  }
  return [...merged.values()];
}

/** 自动处理动作按流程环节排序，同一环节按 order。 */
export function sortHooks(items: IExtension[]): IExtension[] {
  const rank = (x: IExtension) => {
    const i = STAGE_ORDER.indexOf(x.slot.split(',')[0]);
    return i < 0 ? STAGE_ORDER.length : i;
  };
  return [...items].sort((a, b) => rank(a) - rank(b) || (a.order ?? 0) - (b.order ?? 0));
}

export const extensionKeys = (x: IExtension) => [...new Set([
  ...(x.config_section ? [x.config_section] : []),
  ...x.fields.map(f => f.path.split('.')[0]),
])];

/** 显式开关优先；未设置时用目录给出的默认状态（如 STRM 默认关闭）。 */
export const isEnabled = (x: IExtension, toggles: Obj) =>
  typeof toggles[x.name] === 'boolean' ? toggles[x.name] as boolean : x.enabled;

/** 默认规则与各来源实际使用的网盘。 */
export function storagesInUse(cfg: Obj): string[] {
  const d = obj(cfg.defaults);
  return [...new Set([d, ...rows(cfg.sources)].map(r => text(r.storage ?? d.storage)).filter(Boolean))];
}

export const CONNECTION_TONE: Record<string, Tone> = {
  connected: 'success', connecting: 'info', unverified: 'info', stale: 'warning',
  disconnected: 'danger', failed: 'danger', stopped: 'neutral', not_configured: 'neutral', unobserved: 'neutral',
};

/** 设置页各分区：用于未保存提示与页签上的小圆点。 */
export const CONFIG_SECTIONS = [
  { label: '保存目录', tab: 'common', paths: ['save_dirs'] },
  { label: '默认处理方式', tab: 'common', paths: ['defaults.approval_mode', 'defaults.save_to', 'defaults.preferences'] },
  { label: '识别后转发', tab: 'common', paths: ['forward'] },
  { label: '监听来源', tab: 'sources', paths: ['sources'] },
  { label: '识别准确度', tab: 'advanced', paths: ['defaults.confidence_threshold', 'defaults.llm_response_format'] },
  { label: '确认超时', tab: 'advanced', paths: ['approval'] },
];
export const SYSTEM_KEYS = ['save_dirs', 'defaults', 'sources', 'forward', 'approval'];

export function problems(cfg: Obj, extensions: IConfigResponse['extensions'] | undefined): string[] {
  const out: string[] = []; const dirs = obj(cfg.save_dirs); const d = obj(cfg.defaults);
  if (!Object.keys(dirs).length) out.push('请至少保留一个保存目录');
  Object.entries(dirs).forEach(([name, cid]) => {
    if (!name.trim() || name !== name.trim() || !/^[0-9]+$/.test(text(cid))) out.push(`目录「${name}」：名称不能为空或带首尾空格，并需选择一个网盘目录`);
  });
  const checkRule = (r: Obj, label: string) => {
    if (!Object.hasOwn(dirs, text(r.save_to))) out.push(`${label}：选择的保存目录不存在，请重新选择`);
    if (!APPROVAL_OPTIONS.includes(text(r.approval_mode))) out.push(`${label}：确认方式无效`);
    const n = r.confidence_threshold;
    if (typeof n !== 'number' || !Number.isFinite(n) || n < 0 || n > 1) out.push(`${label}：最低置信度必须在 0 到 1 之间`);
    if (text(r.preferences).length > 10000) out.push(`${label}：收藏偏好最多 10000 字`);
  };
  checkRule(d, '默认规则');
  const names = new Set<string>();
  rows(cfg.sources).forEach((s, i) => {
    const name = text(s.bot).trim().toLowerCase();
    const label = `监听来源「${text(s.bot).trim() || i + 1}」`;
    if (!name || names.has(name)) out.push(`${label}：名称不能为空或重复`);
    names.add(name); checkRule({ ...d, ...s }, label);
  });
  const forward = obj(cfg.forward);
  if (forward.enabled && (!text(forward.target).trim() || !text(forward.template).trim())) out.push('识别后转发：开启后需填写转发目标和消息样式');
  const h = obj(cfg.approval).timeout_hours;
  if (typeof h !== 'number' || !Number.isFinite(h) || h <= 0) out.push('确认超时：小时数必须大于 0');
  rows(cfg.routing).forEach((r, i) => {
    if (!Object.hasOwn(dirs, text(r.default ?? r.dir))) out.push(`分类规则 ${i + 1}：目标目录不存在`);
    if (!Object.hasOwn(r, 'default') && !text(r.when).trim()) out.push(`分类规则 ${i + 1}：条件不能为空`);
  });
  for (const x of [...(extensions?.hooks ?? []), ...(extensions?.events ?? [])]) for (const f of x.fields) {
    const v = getPath(cfg, f.path);
    if (v === undefined && !f.required) continue;
    if (f.type === 'number' && (typeof v !== 'number' || !Number.isFinite(v) || v < (f.min ?? -Infinity) || v > (f.max ?? Infinity))) out.push(`「${x.label}」：${f.label}超出允许范围`);
  }
  return [...new Set(out)];
}
