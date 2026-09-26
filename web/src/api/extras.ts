// ===== 控制台增强 API：鉴权 / 日志 / 115 浏览 / 源测试 / 统计 =====
import type { IParsedResource } from '@/lib/types';

function authHeaders(): Record<string, string> {
  return {}; // cookie 由浏览器自动携带
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/${path.replace(/^\//, '')}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init?.headers, ...authHeaders() },
    ...init,
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    let msg = `请求失败（${r.status}）`;
    try { msg = (await r.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export interface IAuthStatus { auth_required: boolean }
export interface ILogResp { lines: string[]; path: string; exists: boolean }
export interface IDirItem { id: number; name: string; pid?: number | null }
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
  logs: (limit = 400, q?: string) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (q) p.set('q', q);
    return req<ILogResp>(`logs?${p}`);
  },
  dirs: (cid: number) => req<{ items: IDirItem[] }>(`storage/dirs?cid=${cid}`),
  sources: () => req<ISourcesResp>('sources'),
  testParse: (text: string, urls: string[], source?: string) =>
    req<{ parsed: IParsedResource | null; decision: string }>('sources/test-parse', {
      method: 'POST', body: JSON.stringify({ text, urls, source }),
    }),
  stats: (days = 30) => req<IStatsResp>(`stats?days=${days}`),
};
