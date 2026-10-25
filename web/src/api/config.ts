import type { IConfigResponse, ISecretStatus } from '@/lib/types';
import { get, patch, post, put } from './request';

export interface ISaveSectionsResp {
  config: Record<string, unknown>;
  revision: string;
  /** 改了 AI / TMDB 的接口地址、代理地址时一并清除的密钥（密钥属于原来的地址）。 */
  cleared_secrets: string[];
}

/** 经代理连一次某项服务的结果。 */
export interface IProxyTestResult { service: string; label: string; ok: boolean; error: string | null; seconds: number }

/** 地址与要测的服务用表单里的值（可以还没保存）。地址和已保存的不同时，已保存的密码不会发过去，要带上 password。 */
export interface IProxyTestForm { url: string; services: string[]; password?: string }

export const configApi = {
  get: () => get<IConfigResponse>('config'),
  validate: (config: Record<string, unknown>) => post<{ ok: boolean }>('config/validate', { config }),
  /** 只提交改动的配置块；revision 不是最新时后端返回 409。 */
  saveSections: (sections: Record<string, unknown>, revision: string) =>
    patch<ISaveSectionsResp>('config/sections', { sections, revision }),
  /** 密钥只能设置、替换或清除（值为空），返回各项是否已设置；修改 Web 密码需带上当前密码。 */
  saveSecrets: (changes: Record<string, string | null>, currentPassword?: string) =>
    put<ISecretStatus>('settings/secrets', { changes, current_password: currentPassword || null }),
  testProxy: ({ password, ...form }: IProxyTestForm) =>
    post<{ results: IProxyTestResult[] }>('proxy/test', password?.trim() ? { ...form, password: password.trim() } : form),
  /** 调用插件在 ui_actions 里声明的动作（如 organizer 的 preview_naming）；返回结构由插件决定。 */
  extensionAction: <T>(name: string, action: string, payload: Record<string, unknown> = {}) =>
    post<T>(`extensions/${encodeURIComponent(name)}/actions/${encodeURIComponent(action)}`, payload),
};
