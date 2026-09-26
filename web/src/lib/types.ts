// EXPORTS: TaskState, ITaskSummary, ITaskDetail, IDashboard, IConnection, IConfigResponse, IOrganizePreviewItem
export type TaskState =
  | 'received'
  | 'parsed'
  | 'pending_approval'
  | 'queued'
  | 'rejected'
  | 'skipped'
  | 'saving'
  | 'need_manual'
  | 'done'
  | 'failed';

export interface IConnection {
  configured: boolean;
  label: string;
}

export interface IDashboard {
  counts: Partial<Record<TaskState, number>>;
  pending: number;
  active: number;
  failed: number;
  done: number;
  simulation: boolean;
  mode: 'simulation' | 'live' | 'not_configured';
  connections: Record<string, IConnection>;
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
}

export interface IConfigResponse {
  config: Record<string, unknown>;
  text: string;
  connections: Record<string, IConnection>;
}

export interface IOrganizePreviewItem {
  src_id: string;
  src_name: string;
  target_dir: string;
  target_name: string;
}
