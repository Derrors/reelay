/** 所有接口共用的请求：拼接子路径前缀、把错误响应转成中文文案、通知登录失效。 */

const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/`;

/** 拼接站内地址；部署在子路径（VITE_BASE_PATH）时自动带上前缀。 */
export const appUrl = (path: string) => `${BASE}${path.replace(/^\//, '')}`;

/** 拼接 API 地址（/api/<path>）。 */
export const apiUrl = (path: string) => appUrl(`api/${path.replace(/^\//, '')}`);

export class ApiError extends Error {
  /** HTTP 状态码；连不上服务时为 0。 */
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
  return () => { unauthorizedListeners.delete(listener); };
}

/** FastAPI 的 detail：字符串原样使用；校验错误数组拼成「字段：原因」。 */
function detailText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (!Array.isArray(detail)) return null;
  return detail.map((item: { loc?: unknown[]; msg?: string }) => {
    // loc 第一段是参数位置（body / query / path），对用户没有意义
    const field = (item.loc ?? []).slice(1).join('.');
    return `${field ? `${field}：` : ''}${item.msg ?? '输入无效'}`;
  }).join('；');
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const route = path.replace(/^\//, '');
  let response: Response;
  try {
    response = await fetch(apiUrl(route), {
      credentials: 'same-origin',
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiError('无法连接到服务，请检查网络后重试', 0);
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      message = detailText(((await response.json()) as { detail?: unknown }).detail) ?? message;
    } catch {
      // 非 JSON 错误响应（如反代的 502 页面），保留通用文案
    }
    // 登录接口的 401 是密码错误；其他接口的 401 表示会话失效
    if (response.status === 401 && !route.startsWith('auth/login')) {
      message = '登录已失效，请重新登录控制台';
      for (const listener of [...unauthorizedListeners]) listener();
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const get = <T>(path: string) => request<T>(path);

const withBody = (method: string) => <T>(path: string, body?: unknown) =>
  request<T>(path, { method, body: JSON.stringify(body ?? {}) });

export const post = withBody('POST');
export const put = withBody('PUT');
export const patch = withBody('PATCH');
export const del = <T>(path: string) => request<T>(path, { method: 'DELETE' });

/** 查询串：跳过空值，没有参数时为空串。 */
export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  }
  // 用 toString() 判断是否为空：URLSearchParams.size 在 Safari 17 之前不存在
  const text = search.toString();
  return text ? `?${text}` : '';
}
