import { get, post } from './request';

export interface IAuthStatus {
  auth_required: boolean;
  authenticated?: boolean;
  /** 还没有设置登录密码：先显示设置密码页。 */
  setup_required?: boolean;
  /** 这个地址不能做首次设置的原因（如用域名打开、不在局域网内）。 */
  setup_blocked?: string | null;
}

export const authApi = {
  status: () => get<IAuthStatus>('auth/status'),
  /** 密码错误时为 401，尝试过于频繁时为 429（detail 里写了几秒后再试）。 */
  login: (password: string) => post<{ ok: boolean }>('auth/login', { password }),
  logout: () => post<{ ok: boolean }>('auth/logout'),
  /** 首次设置登录密码，成功后已登录；已经设置过时为 409，太短时为 422。 */
  setup: (password: string) => post<{ ok: boolean }>('auth/setup', { password }),
};
