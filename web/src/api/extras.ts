// ===== 控制台增强 API：鉴权 / 日志 / 115 浏览 / 源测试 / 统计 =====
import type { IParsedResource } from '@/lib/types';
import { apiUrl } from './client';

function authHeaders(): Record<string, string> {
  return {}; // cookie 由浏览器自动携带
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(apiUrl(path), {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init?.headers, ...authHeaders() },
    ...init,
  });
  if (r.status === 401) throw new Error('登录已失效，请重新登录控制台');
  if (!r.ok) {
    let msg = `请求失败（${r.status}）`;
    try {
      const detail: unknown = (await r.json()).detail;
      if (typeof detail === 'string') msg = detail;
      else if (Array.isArray(detail)) msg = detail.map((x: { msg?: string }) => x.msg ?? '输入无效').join('；');
    } catch { /* 非JSON响应沿用状态码 */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export interface IAuthStatus { auth_required: boolean; authenticated?: boolean }
export interface ILogResp { lines: string[]; path: string; exists: boolean }
export interface IDirItem { id: string; name: string; pid?: string | null }

// ===== 115 混合鉴权 =====
export interface IAuth115Side {
  state: 'unknown' | 'active' | 'expired' | 'error';
  configured: boolean;
  source?: 'file' | 'env' | 'none';
  error?: string;
  checked_at?: number | null;
  has_access_token?: boolean;
  has_refresh_token?: boolean;
  app_id?: number | null;
  updated_at?: number;
}
export interface IAuth115Traffic {
  cooling_down: boolean;
  cooldown_until?: number | null;
  cooldown_remaining?: number;
  cooldown_reason?: string;
  open_qps?: number;
  cookie_qps?: number;
  share_qps?: number;
}
export interface IAuth115Status {
  cookie: IAuth115Side;
  oauth: IAuth115Side;
  traffic?: IAuth115Traffic;
  ready: boolean;
  writes_via: 'oauth' | 'cookie' | 'none';
}
export interface IQrStart { uid: string; qrcode_url: string; app_id?: number; app?: string; expires_at: number }
export interface IQrStatus { status: number; message: string; authenticated: boolean; state?: string }

export interface IStatsResp {
  counts: Record<string, number>;
  daily: Array<{ d: string; state: string; n: number }>;
  top_titles: Array<{ title: string; state: string; n: number }>;
  estimated_done_gb: number;
}
export interface ISourcesResp {
  sources: Array<Record<string, unknown>>;
  save_dirs: Record<string, string>;
}

export const extras = {
  authStatus: () => req<IAuthStatus>('auth/status'),
  login: (password: string) => req<{ ok: boolean }>('auth/login', {
    method: 'POST', body: JSON.stringify({ password }),
  }),
  logout: () => req<{ ok: boolean }>('auth/logout', { method: 'POST' }),
  // ---- 115 混合鉴权 ----
  auth115Status: () => req<IAuth115Status>('auth115/status'),
  auth115Ping: () => req<{ cookie: string; oauth: string; status: IAuth115Status }>(
    'auth115/ping', { method: 'POST' }),
  auth115SetCookie: (cookies: string) =>
    req<{ ok: boolean; state: string }>('auth115/cookie', {
      method: 'PUT', body: JSON.stringify({ cookies }),
    }),
  auth115CookieQrStart: () => req<IQrStart>('auth115/cookie/qr', { method: 'POST' }),
  auth115CookieQrStatus: (uid: string) =>
    req<IQrStatus>(`auth115/cookie/qr?uid=${encodeURIComponent(uid)}`),
  auth115CookieQrImageUrl: (uid: string) =>
    apiUrl(`auth115/cookie/qr/image?uid=${encodeURIComponent(uid)}`),
  auth115QrStart: () => req<IQrStart>('auth115/oauth/qr', { method: 'POST' }),
  auth115QrStatus: (uid: string) =>
    req<IQrStatus>(`auth115/oauth/qr?uid=${encodeURIComponent(uid)}`),
  auth115QrImageUrl: (uid: string) =>
    apiUrl(`auth115/oauth/qr/image?uid=${encodeURIComponent(uid)}`),
  auth115ClearOauth: () => req<{ ok: boolean }>('auth115/oauth', { method: 'DELETE' }),
  logs: (limit = 400, q?: string) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (q) p.set('q', q);
    return req<ILogResp>(`logs?${p}`);
  },
  dirs: (cid: string, offset = 0) => req<{ items: IDirItem[]; path: IDirItem[]; next_offset: number | null; via: string }>(`storage/dirs?cid=${encodeURIComponent(cid)}&offset=${offset}`),
  sources: () => req<ISourcesResp>('sources'),
  testParse: (text: string, urls: string[], source?: string) =>
    req<{ parsed: IParsedResource | null; decision: string }>('sources/test-parse', {
      method: 'POST', body: JSON.stringify({ text, urls, source }),
    }),
  stats: (days = 30) => req<IStatsResp>(`stats?days=${days}`),
  advancedTasks: (q?: string, mediaType?: string) => {
    const p = new URLSearchParams();
    if (q) p.set('q', q);
    if (mediaType) p.set('media_type', mediaType);
    return req<IAdvancedResp>(`tasks/advanced?${p.toString()}`);
  },
  storageFile: (fid: number | string) => req<IStorageFile>(`storage/file?fid=${fid}`),
  batch: (taskIds: string[], action: 'approve' | 'reject' | 'retry') =>
    req<{ done: number; failed: Array<{ task_id: string; error: string }> }>('tasks/batch', {
      method: 'POST', body: JSON.stringify({ task_ids: taskIds, action }),
    }),
};

export interface ITitleGroup {
  title: string; media_type: string | null; year: number | null;
  episodes: number[]; tasks: number; bytes: number;
}
export interface IAdvancedResp {
  groups: ITitleGroup[]; total_done: number; estimated_gb: number;
}
export interface IStorageFile {
  id: string; name: string; size: number | null; sha1: string | null;
  parent_id: number | null; is_dir?: boolean; pickcode?: string | null;
}

Object.assign(extras, {
  advancedTasks: (q?: string, mediaType?: string) => {
    const p = new URLSearchParams();
    if (q) p.set('q', q);
    if (mediaType) p.set('media_type', mediaType);
    return req<IAdvancedResp>(`tasks/advanced?${p.toString()}`);
  },
  storageFile: (fid: number | string) => req<IStorageFile>(`storage/file?fid=${fid}`),
  batch: (taskIds: string[], action: 'approve' | 'reject' | 'retry') =>
    req<{ done: number; failed: Array<{ task_id: string; error: string }> }>('tasks/batch', {
      method: 'POST', body: JSON.stringify({ task_ids: taskIds, action }),
    }),
});
