/** 设置 / 插件页共用的配置草稿工具与说明文案（只在这两个页面使用）。 */
import { PROVIDER_LABELS, STAGE_LABELS } from '@/lib/status';
import type { IExtension, IExtensionCatalog, ISecretStatus } from '@/lib/types';
import { obj, rows, text, type Obj } from '@/lib/values';

export type Catalog = IExtensionCatalog;

export const APPROVAL_OPTIONS = ['manual', 'auto'];
export const APPROVAL_HINTS: Record<string, string> = {
  manual: '默认：每条符合要求的资源都等你确认后再转存。',
  auto: '适合信任的来源：AI 识别、规则都通过就直接转存；拿不准的仍交给你确认。',
};
/** 来源单独设置「Bot 通知」：开 / 关（未设置时跟随默认）。 */
export const NOTIFY_OPTIONS = [{ value: 'on', label: '开' }, { value: 'off', label: '关' }];
export const notifyValue = (v: unknown) => (v === undefined || v === null ? '' : v ? 'on' : 'off');

export const SLOT_ORDER = ['source', 'parser', 'decision', 'storage'];
/** 只在来源卡片上设置的能力；defaults 里的值只是来源没写时的兜底。 */
export const PER_SOURCE_SLOTS = ['parser', 'decision'];

const PROVIDER_HINTS: Record<string, Record<string, string>> = {
  decision: {
    default: '按确认方式与过滤规则决定是否转存，不额外调用 AI。',
    subscription: '在规则判断之上，已订阅剧集的新集直接转存，不再等你确认；过滤规则与置信度照常生效。',
    llm: '在规则与订阅放行之外，再让 AI 按你的收藏偏好和近期的批准记录筛选；不会绕过过滤规则、低置信度与人工确认。',
  },
  parser: {
    llm: '用大模型理解消息，识别片名、季集、清晰度，准确率更高。',
    regex: '按规则提取信息，不调用 AI，适合格式规整的来源；每个来源可以在来源设置里写自己的识别规则。',
  },
  source: {
    telegram: '用你自己的 Telegram 账号监听资源 Bot 与频道。',
  },
  storage: { p115: '转存到你的 115 网盘。' },
  notify: { console: '配置了控制 Bot 时推送确认卡片与通知，否则写入运行日志。' },
};

/** 能力实现的中文名；未知实现回退到目录里的 label，再回退到内部名。 */
export function providerLabel(slot: string, name: string, catalog?: Catalog | null): string {
  if (!name) return '';
  const known = PROVIDER_LABELS[slot]?.[name];
  if (known) return known;
  return catalog?.providers[slot]?.find(x => x.name === name)?.label || name;
}

export function providerHint(slot: string, name: string, catalog?: Catalog | null): string {
  return PROVIDER_HINTS[slot]?.[name] ?? catalog?.providers[slot]?.find(x => x.name === name)?.description ?? '';
}

const STAGE_ORDER = ['after_parse', 'before_save', 'after_save'];
export const stageNames = (slot: string) => slot.split(',').map(s => s.trim()).filter(Boolean)
  .map(s => STAGE_LABELS[s] ?? s);

/** 自动处理动作按流程环节排序，同一环节按 order。 */
export function sortHooks(items: IExtension[]): IExtension[] {
  const rank = (x: IExtension) => {
    const i = STAGE_ORDER.indexOf(x.slot.split(',')[0]);
    return i < 0 ? STAGE_ORDER.length : i;
  };
  return [...items].sort((a, b) => rank(a) - rank(b) || (a.order ?? 0) - (b.order ?? 0));
}

/** plugins.<名>.enabled 优先；未设置时用目录给出的默认状态（扩展可声明默认关闭）。 */
export const isEnabled = (x: IExtension, plugins: Obj) => {
  const value = obj(plugins[x.name]).enabled;
  return typeof value === 'boolean' ? value : x.enabled;
};

/** 默认规则与各来源实际使用的网盘。 */
export function storagesInUse(cfg: Obj): string[] {
  const d = obj(cfg.defaults);
  return [...new Set([d, ...rows(cfg.sources)].map(r => text(r.storage ?? d.storage)).filter(Boolean))];
}

/** 设置页各分区：用于未保存提示、页签上的小圆点，以及把后端的校验错误归到分区。 */
export const CONFIG_SECTIONS = [
  { label: '保存目录', tab: 'common', paths: ['save_dirs'] },
  { label: '默认处理方式', tab: 'common', paths: ['defaults.approval_mode', 'defaults.bot_notify', 'defaults.save_to', 'defaults.preferences'] },
  { label: '识别后转发', tab: 'common', paths: ['forward'] },
  { label: '监听来源', tab: 'sources', paths: ['sources'] },
  { label: '识别准确度', tab: 'advanced', paths: ['defaults.confidence_threshold', 'defaults.llm_response_format'] },
  { label: '确认超时', tab: 'advanced', paths: ['approval'] },
  { label: 'AI 能力', tab: 'advanced', paths: ['ai'] },
  { label: '版本偏好', tab: 'advanced', paths: ['release'] },
  { label: 'AI 服务', tab: 'services', paths: ['llm'] },
  { label: 'TMDB', tab: 'services', paths: ['tmdb'] },
  { label: 'Telegram', tab: 'telegram', paths: ['telegram'] },
  { label: '115 请求限速', tab: 'connections', paths: ['p115'] },
  { label: '运行参数', tab: 'system', paths: ['runtime'] },
];
/** 设置页负责的配置块（插件页负责 providers、defaults 与 plugins）。追剧订阅与不再接收的作品存在数据库里，不在配置中。 */
export const SYSTEM_KEYS = ['save_dirs', 'defaults', 'sources', 'forward', 'approval', 'ai', 'release',
  'llm', 'tmdb', 'telegram', 'p115', 'runtime'];
export const PLUGIN_KEYS = ['providers', 'defaults', 'plugins'];

/**
 * 后端的校验错误（如「llm.model 不能为空」「sources[0] bot 重复」）前面标上分区名，方便找到要改的地方。
 * 保存前不在前端重复做范围检查：/api/config/validate 与保存接口用的是同一套校验，以后端为准。
 */
export function describeConfigError(message: string): string {
  const key = /^([a-z_0-9]+)(?:[.[\s]|$)/.exec(message)?.[1];
  if (!key) return message;
  const section = CONFIG_SECTIONS.find(s => s.paths.some(p => p === key || p.startsWith(`${key}.`))
    && (key !== 'defaults' || s.paths.some(p => message.startsWith(p))));
  return section ? `「${section.label}」${message}` : message;
}

/** 接口地址 → 属于它的密钥：地址改了，后端保存时会清除这个密钥。 */
export const URL_SECRETS: Array<{ path: string; secret: string; label: string }> = [
  { path: 'llm.base_url', secret: 'llm_api_key', label: 'AI 服务' },
  { path: 'tmdb.base_url', secret: 'tmdb_api_key', label: 'TMDB' },
];

/** 比较接口地址时去掉首尾空白与末尾的斜杠（与后端一致）。 */
export const normalizeUrl = (value: unknown) => text(value).trim().replace(/\/+$/, '');

/** 保存后后端清除的密钥（cleared_secrets）标为未设置。 */
export function clearSecrets(status: ISecretStatus, cleared: readonly string[]): ISecretStatus {
  if (!cleared.length) return status;
  return Object.fromEntries(Object.entries(status).map(([name, item]) => [name, cleared.includes(name) ? { ...item, set: false } : item]));
}

/** 网盘路径「/影视/剧集」→「影视 / 剧集」；根目录为空串。 */
export const pathLabel = (path: string) => path.split('/').filter(Boolean).join(' / ');

/** 保存目录编号 → 显示名：来自 GET /api/config 的 save_dir_paths（按当时的配置对应到编号）。 */
export function dirNamesFrom(config: Obj, paths: Record<string, string>): Record<string, string> {
  const names: Record<string, string> = { '0': '根目录' };
  for (const [alias, cid] of Object.entries(obj(config.save_dirs))) {
    const path = paths[alias];
    if (path !== undefined && text(cid) !== '0') names[text(cid)] = pathLabel(path) || '根目录';
  }
  return names;
}
