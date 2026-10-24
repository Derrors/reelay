import type { IDashboard, IDelivery } from '@/lib/types';
import { get, post } from './request';

export const dashboardApi = {
  get: () => get<IDashboard>('dashboard'),
  failedDeliveries: () => get<{ items: IDelivery[] }>('deliveries?status=failed&limit=500'),
  retryDelivery: (id: number) => post<{ ok: boolean }>(`deliveries/${id}/retry`),
};
