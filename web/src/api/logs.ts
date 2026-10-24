import { get, post, query } from './request';

export interface ILogResp {
  lines: string[];
  path: string;
  exists: boolean;
}

export const logApi = {
  list: (limit = 400, q?: string) => get<ILogResp>(`logs${query({ limit, q })}`),
  /** 清空运行日志（含轮转留下的旧文件），返回释放的字节数。 */
  clear: () => post<{ ok: boolean; freed: number }>('logs/clear'),
};
