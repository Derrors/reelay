import { get, post } from './request';

export interface IAuthStatus {
  auth_required: boolean;
  authenticated?: boolean;
}

export const authApi = {
  status: () => get<IAuthStatus>('auth/status'),
  /** 密码错误时为 401，尝试过于频繁时为 429（detail 里写了几秒后再试）。 */
  login: (password: string) => post<{ ok: boolean }>('auth/login', { password }),
  logout: () => post<{ ok: boolean }>('auth/logout'),
};
