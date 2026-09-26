import type {
  IConfigResponse,
  IDashboard,
  IOrganizePreviewItem,
  IParsedResource,
  ITaskDetail,
  ITaskSummary,
  TaskState,
} from '@/lib/types';

const BASE = `${(import.meta.env.APP_CLIENT_BASE_PATH || '').replace(/\/$/, '')}/`;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}api/${path.replace(/^\//, '')}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // 非 JSON 错误响应，保留通用文案
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  dashboard: () => request<IDashboard>('dashboard'),
  tasks: (filters: { state?: TaskState | ''; source?: string; query?: string } = {}) => {
    const params = new URLSearchParams();
    if (filters.state) params.set('state', filters.state);
    if (filters.source) params.set('source', filters.source);
    if (filters.query) params.set('query', filters.query);
    const suffix = params.size ? `?${params.toString()}` : '';
    return request<{ items: ITaskSummary[] }>(`tasks${suffix}`);
  },
  task: (taskId: string) => request<ITaskDetail>(`tasks/${taskId}`),
  action: (taskId: string, action: 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize', body?: unknown) =>
    request<{ ok: boolean }>(`tasks/${taskId}/${action}`, {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    }),
  patchParsed: (taskId: string, values: Partial<IParsedResource>) =>
    request<ITaskDetail>(`tasks/${taskId}/parsed`, {
      method: 'PATCH',
      body: JSON.stringify({ values }),
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
