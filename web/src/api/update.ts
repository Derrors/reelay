import type { IUpdateStatus } from '@/lib/types';
import { get, post } from './request';

export const updateApi = {
  /** 结果在服务端缓存 6 小时；带一键升级的进度。 */
  get: () => get<IUpdateStatus>('update'),
  /** 立即联网查询一次。 */
  check: () => post<IUpdateStatus>('update/check'),
  /** 源码部署：升级到最新正式版并重启，后台执行；Docker 部署返回 409。 */
  apply: () => post<IUpdateStatus>('update/apply'),
};

/** 一键升级进行中（含正在重启）。 */
export const isUpgrading = (status?: IUpdateStatus) =>
  status?.upgrade?.state?.status === 'running' || status?.upgrade?.state?.status === 'restarting';
