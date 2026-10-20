// EXPORTS: TaskState, ITaskSummary, ITaskDetail, IDashboard, IConnection, IConfigResponse, IOrganizePreviewItem
export type TaskState =
  | 'received'
  | 'parsed'
  | 'pending_approval'
  | 'queued'
  | 'rejected'
  | 'skipped'
  | 'saving'
  | 'stored'
  | 'organizing'
  | 'need_manual'
  | 'done'
  | 'failed';

export interface IConnection {
  configured: boolean;
  label: string;
  status: string;
  observed_at?: number;
  error?: string;
}

export interface IRuntimeHealth {
  status: string;
  updated_at: number;
  stale: boolean;
  config_revision?: string;
  config_error?: string | null;
  inflight?: number;
}

export interface IDelivery {
  id: number;
  task_id: string;
  kind: 'event' | 'notify';
  event: string;
  target: string;
  status: string;
  attempts: number;
  error: string | null;
}

export interface IDashboard {
  counts: Partial<Record<TaskState, number>>;
  pending: number;
  active: number;
  failed: number;
  done: number;
  simulation: boolean;
  mode: 'simulation' | 'live' | 'not_configured' | 'not_running';
  connections: Record<string, IConnection>;
  runtime: Record<string, IRuntimeHealth>;
  deliveries: Record<string, number>;
  /** 进行中的系统告警（如 115 凭证失效暂停转存、消息源断开）。 */
  alerts?: IAlert[];
  /** 最近 24 小时：新收消息数与入库数。 */
  recent?: { received_24h: number; done_24h: number };
  config_revision: string;
}

export interface IAlert {
  key: string;
  text: string;
  since: number;
  updated_at: number;
}

export interface IParsedResource {
  is_media?: boolean;
  is_ad?: boolean;
  confidence?: number;
  media_type?: string | null;
  title?: string | null;
  year?: number | null;
  season?: number | null;
  episodes?: number[];
  genres?: string[];
  genre_ids?: number[];
  origin_country?: string[];
  resolution?: string | null;
  size?: string | null;
  share_code?: string | null;
  receive_code?: string | null;
  reason?: string | null;
  raw_fields?: Record<string, unknown>;
}

export interface ITaskSummary {
  task_id: string;
  state: TaskState;
  source: string | null;
  msg_id: string | null;
  title: string | null;
  error: string | null;
  retries: number;
  created_at: number;
  updated_at: number;
  media_type?: string | null;
  year?: number | null;
  season?: number | null;
  episodes?: number[];
  /** 跨多季合集的（季, 集）；单季为 null。 */
  episode_pairs?: number[][] | null;
  resolution?: string | null;
  size?: string | null;
  poster_path?: string | null;
  save_dir_alias?: string | null;
}

/** 收件箱卡片：/api/inbox 一次返回展示所需字段（不含分享链接）。 */
export interface IShareProbe {
  /** ok 已确定季与集；available 分享有效（消息写了集数，不需要读取季集）；issue 转存时需人工；unavailable 分享失效；
   *  incomplete 内容过多未读完；error 读取出错待重试。 */
  status: 'ok' | 'available' | 'issue' | 'unavailable' | 'incomplete' | 'error' | null;
  note: string | null;
  videos: number | null;
  from_share: boolean;
}

export interface IInboxItem {
  task_id: string;
  state: TaskState;
  source: string | null;
  created_at: number;
  updated_at: number;
  error: string | null;
  title: string | null;
  year: number | null;
  media_type: string | null;
  season: number | null;
  episodes: number[];
  resolution: string | null;
  size: string | null;
  reason: string | null;
  confidence: number;
  confidence_caps: string[];
  fallback: boolean;
  tmdb_matched: boolean | null;
  poster_path: string | null;
  save_dir_alias: string | null;
  /** 待审批任务不手动改目录时将存入的位置，如「剧集/国产剧集」（目录别名/分类）。 */
  auto_dir: string | null;
  /** 同一位置在网盘中的真实路径，如「/测试/剧集/国产剧集」；尚未读取到时为 null。 */
  auto_path: string | null;
  /** 跨多季合集的（季, 集）；单季为 null。 */
  episode_pairs: number[][] | null;
  /** 消息没写集数的剧集读取分享的结果；from_share 表示 season/episodes/size 来自分享。 */
  share: IShareProbe | null;
  preview: string;
  storage_phase: string | null;
  has_landed: boolean;
  organize_error: string | null;
  /** 以下只在待确认（pending_approval）里返回。 */
  release?: IRelease;
  /** 版本信息的中文标签，如 ["简体中字", "内封字幕", "杜比视界"]。 */
  release_tags?: string[];
  /** 同一集（或同一部电影）有多个待确认版本时的择优结果；只有一个版本时为 null。 */
  version?: IVersionPick | null;
  /** 是否属于已订阅的剧。 */
  subscribed?: boolean;
  /** 审批建议；关闭建议（ai.advice=false）时为 null。 */
  advice?: IAdvice | null;
  /** 待人工任务的失败解释（失败解释扩展写入）。 */
  explanation?: IExplanation | null;
}

export interface IRelease {
  subtitles: string[];
  subtitle_format: string | null;
  audio: string[];
  hdr: string[];
  source: string | null;
  codec: string | null;
  packaging: string | null;
  group: string | null;
}

export interface IVersionPick {
  count: number;
  best: boolean;
  best_task_id: string;
}

export type AdviceAction = 'approve' | 'reject' | 'review';

export interface IAdvice {
  action: AdviceAction;
  /** 「建议批准」「建议忽略」「需要你看一下」。 */
  label: string;
  text: string;
  /** rule：规则写的理由；ai：AI 按同样的事实措辞。 */
  source: 'rule' | 'ai';
}

export interface IExplanation {
  action: string;
  /** 建议的操作，如「核验转存」「修正片名或季集后重新整理」。 */
  label: string;
  text: string;
  source: 'rule' | 'ai';
  state: TaskState;
  at: number;
}

export interface ITaskContext {
  task_id: string;
  state: TaskState;
  raw: {
    source: string;
    msg_id: string;
    text: string;
    urls: string[];
    received_at: number;
    extra: Record<string, unknown>;
  };
  parsed: IParsedResource | null;
  decision: 'save' | 'ask' | 'skip' | null;
  save_dir_alias: string | null;
  save_dir_cid: string | null;
  result: Record<string, unknown> | null;
  source_rule: Record<string, unknown>;
  meta: Record<string, Record<string, unknown>>;
  trail: string[];
}

export interface ITaskDetail extends ITaskSummary {
  context: ITaskContext;
  auto_dir?: string | null;
  auto_path?: string | null;
  /** 失败或待人工任务的原因与建议操作。 */
  explanation?: IExplanation | null;
}

export interface IExtensionField {
  path: string; label: string; type: 'string' | 'number' | 'boolean';
  choices?: string[]; min?: number; max?: number; required?: boolean;
  /** 未配置时后端实际使用的值（扩展可选声明）。 */
  default?: unknown;
}
export interface IExtension {
  name: string; kind: 'provider' | 'hook' | 'event'; slot: string; label: string;
  description: string; enabled: boolean; order: number | null;
  fields: IExtensionField[]; config_section: string | null; config_type?: string | null; required_storage: string | null;
}
export interface IExtensionCatalog {
  providers: Record<string, IExtension[]>; hooks: IExtension[]; events: IExtension[];
  /** 外部扩展导入或实例化失败的记录。 */
  load_errors?: Array<{ source: string; error: string }>;
}
/** 密钥名称 → 显示名与是否已设置（原值不会返回）。 */
export type ISecretStatus = Record<string, { label: string; set: boolean }>;
export interface IConfigResponse {
  revision: string;
  secrets: ISecretStatus;
  extensions: IExtensionCatalog;
  config: Record<string, unknown>;
  text: string;
  connections: Record<string, IConnection>;
  config_error: string | null;
}

export interface IOrganizePreviewItem {
  src_id: string;
  src_name: string;
  target_dir: string;
  target_name: string;
}
