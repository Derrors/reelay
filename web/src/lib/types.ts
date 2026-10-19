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
  resolution?: string | null;
  size?: string | null;
  poster_path?: string | null;
  save_dir_alias?: string | null;
}

/** 收件箱卡片：/api/inbox 一次返回展示所需字段（不含分享链接）。 */
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
  /** 待审批任务不手动改目录时将存入的位置，如「剧集/国产剧集」。 */
  auto_dir: string | null;
  preview: string;
  storage_phase: string | null;
  has_landed: boolean;
  organize_error: string | null;
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
export interface IConfigResponse {
  revision: string;
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
