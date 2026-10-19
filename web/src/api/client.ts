import type {
  IConfigResponse,
  IDashboard,
  IDelivery,
  IOrganizePreviewItem,
  IParsedResource,
  ITaskDetail,
  ITaskSummary,
  TaskState,
} from '@/lib/types';

const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/`;

/** 拼接 API 地址；部署在子路径（VITE_BASE_PATH）时自动带上前缀。 */
export const apiUrl = (path: string) => `${BASE}api/${path.replace(/^\//, '')}`;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
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
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  dashboard: () => request<IDashboard>('dashboard'),
  deliveries: () => request<{ items: IDelivery[] }>('deliveries?status=failed&limit=500'),
  retryDelivery: (id: number) => request<{ ok: boolean }>(`deliveries/${id}/retry`, { method: 'POST' }),
  tasks: (filters: { state?: TaskState | ''; source?: string; query?: string } = {}) => {
    const params = new URLSearchParams();
    if (filters.state) params.set('state', filters.state);
    if (filters.source) params.set('source', filters.source);
    if (filters.query) params.set('query', filters.query);
    const suffix = params.size ? `?${params.toString()}` : '';
    return request<{ items: ITaskSummary[] }>(`tasks${suffix}`);
  },
  task: (taskId: string) => request<ITaskDetail>(`tasks/${taskId}`),
  action: (taskId: string, action: 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize' | 'reconcile', body?: unknown) =>
    request<{ ok: boolean }>(`tasks/${taskId}/${action}`, {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    }),
  patchParsed: (taskId: string, values: Partial<IParsedResource>) =>
    request<ITaskDetail>(`tasks/${taskId}/parsed`, {
      method: 'PATCH',
      body: JSON.stringify({ values }),
    }),
  organizerTemplatePreview: (kind: 'movie' | 'tv', template: string) =>
    request<{ path: string }>('extensions/organizer/preview', {
      method: 'POST', body: JSON.stringify({ kind, template }),
    }),
  dirRouterPreview: (categories: Record<string, unknown>, media_type: string, genre_ids: number[], origin_country: string[]) =>
    request<{ path: string | null }>('extensions/dir-router/preview', {
      method: 'POST', body: JSON.stringify({ categories, media_type, genre_ids, origin_country }),
    }),
  validateConfig: (config: Record<string, unknown>) => request<{ ok: boolean }>('config/validate', {
    method: 'POST', body: JSON.stringify({ config }),
  }),
  saveSections: (sections: Record<string, unknown>, revision: string) =>
    request<{ config: Record<string, unknown>; revision: string }>('config/sections', {
      method: 'PATCH', body: JSON.stringify({ sections, revision }),
    }),
  config: () => request<IConfigResponse>('config'),
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
  organizePreview: (parsed: IParsedResource, landed: Array<{ id: string; name: string }>) =>
    request<{ items: IOrganizePreviewItem[] }>('organize/preview', {
      method: 'POST',
      body: JSON.stringify({ parsed, landed }),
    }),
};
