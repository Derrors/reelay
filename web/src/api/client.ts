import type {
  IConfigResponse,
  ISecretStatus,
  IDashboard,
  IInboxItem,
  IDelivery,
  IParsedResource,
  ITaskDetail,
  ITaskSummary,
  TaskState,
} from '@/lib/types';

const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/`;

/** 拼接 API 地址；部署在子路径（VITE_BASE_PATH）时自动带上前缀。 */
/** 任务操作（POST /api/tasks/<id>/<action>）。accept-partial：转存只到一部分时按已到的文件继续；
 *  abandon：放弃这次转存，删除暂存目录后忽略。 */
export type TaskAction = 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize' | 'reconcile'
  | 'accept-partial' | 'abandon';

export const apiUrl = (path: string) => `${BASE}api/${path.replace(/^\//, '')}`;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const unauthorizedListeners = new Set<() => void>();

/**
 * 订阅「登录已失效」：任一接口返回 401 时通知。App 据此切回登录页，各页面随之卸载、停止轮询，
 * 而不是在后台一直拿着失效的会话请求。返回取消订阅函数。
 */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

/** 接口返回 401 时调用（登录接口本身的 401 是密码错误，不算）。 */
export function reportUnauthorized(): void {
  for (const listener of [...unauthorizedListeners]) listener();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.join('.') ?? ''}：${item.msg ?? '输入无效'}`).join('；');
    } catch {
      // 非 JSON 错误响应，保留通用文案
    }
    if (response.status === 401) reportUnauthorized();
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  dashboard: () => request<IDashboard>('dashboard'),
  deliveries: () => request<{ items: IDelivery[] }>('deliveries?status=failed&limit=500'),
  retryDelivery: (id: number) => request<{ ok: boolean }>(`deliveries/${id}/retry`, { method: 'POST' }),
  /** state 可传逗号分隔的多个状态；limit/offset 用于分页加载。 */
  tasks: (filters: { state?: TaskState | string; source?: string; query?: string; limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.state) params.set('state', filters.state);
    if (filters.source) params.set('source', filters.source);
    if (filters.query) params.set('query', filters.query);
    if (filters.limit) params.set('limit', String(filters.limit));
    if (filters.offset) params.set('offset', String(filters.offset));
    const suffix = params.size ? `?${params.toString()}` : '';
    return request<{ items: ITaskSummary[] }>(`tasks${suffix}`);
  },
  inbox: (state: 'pending_approval' | 'need_manual' = 'pending_approval') =>
    request<{ items: IInboxItem[]; save_dirs: string[]; save_dir_paths: Record<string, string> }>(`inbox?state=${state}`),
  task: (taskId: string) => request<ITaskDetail>(`tasks/${taskId}`),
  action: (taskId: string, action: TaskAction, body?: unknown) =>
    request<{ ok: boolean }>(`tasks/${taskId}/${action}`, {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    }),
  patchParsed: (taskId: string, values: Partial<IParsedResource>) =>
    request<ITaskDetail>(`tasks/${taskId}/parsed`, {
      method: 'PATCH',
      body: JSON.stringify({ values }),
    }),
  /** 调用插件在 ui_actions 里声明的动作（如 organizer 的 preview_naming）；返回结构由插件决定。 */
  extensionAction: <T>(name: string, action: string, payload: Record<string, unknown> = {}) =>
    request<T>(`extensions/${encodeURIComponent(name)}/actions/${encodeURIComponent(action)}`, {
      method: 'POST', body: JSON.stringify(payload),
    }),
  validateConfig: (config: Record<string, unknown>) => request<{ ok: boolean }>('config/validate', {
    method: 'POST', body: JSON.stringify({ config }),
  }),
  saveSections: (sections: Record<string, unknown>, revision: string) =>
    request<{ config: Record<string, unknown>; revision: string }>('config/sections', {
      method: 'PATCH', body: JSON.stringify({ sections, revision }),
    }),
  config: () => request<IConfigResponse>('config'),
  /** 密钥只能设置、替换或清除（值为空），返回各项是否已设置；修改 Web 密码需带上当前密码。 */
  saveSecrets: (changes: Record<string, string | null>, currentPassword?: string) =>
    request<ISecretStatus>('settings/secrets', {
      method: 'PUT', body: JSON.stringify({ changes, current_password: currentPassword || null }),
    }),
  saveConfig: (config: Record<string, unknown>) =>
    request<{ config: Record<string, unknown> }>('config', {
      method: 'PUT',
      body: JSON.stringify({ config }),
    }),
  saveConfigText: (text: string) =>
    request<{ config: Record<string, unknown>; text: string }>('config/text', {
      method: 'PUT',
      body: JSON.stringify({ text }),
    }),
};
