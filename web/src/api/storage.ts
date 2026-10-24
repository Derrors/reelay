// 115 网盘：只读浏览目录，以及扫码登录 / 文件管理授权两种凭证
import { apiUrl, del, get, post, put, query } from './request';

export interface IDirItem {
  id: string;
  name: string;
}

export interface IDirListing {
  items: IDirItem[];
  /** 从根目录到当前目录的路径，含根目录本身（id 为 0）。 */
  path: IDirItem[];
  next_offset: number | null;
  via: string;
}

export interface IAuth115Side {
  state: 'unknown' | 'active' | 'expired' | 'error';
  configured: boolean;
  source?: 'file' | 'none';
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

/** 两种扫码：cookie 是扫码登录（接收分享），oauth 是文件管理授权（改名整理）。 */
export type QrMode = 'cookie' | 'oauth';

const qrBase = (mode: QrMode) => (mode === 'cookie' ? 'auth115/cookie/qr' : 'auth115/oauth/qr');

export const storageApi = {
  dirs: (cid: string, offset = 0) => get<IDirListing>(`storage/dirs${query({ cid, offset })}`),
  auth115Status: () => get<IAuth115Status>('auth115/status'),
  auth115Ping: () => post<{ cookie: string; oauth: string; status: IAuth115Status }>('auth115/ping'),
  auth115SetCookie: (cookies: string) => put<{ ok: boolean; state: string }>('auth115/cookie', { cookies }),
  qrStart: (mode: QrMode) => post<IQrStart>(qrBase(mode)),
  qrStatus: (mode: QrMode, uid: string) => get<IQrStatus>(`${qrBase(mode)}${query({ uid })}`),
  qrImageUrl: (mode: QrMode, uid: string) => apiUrl(`${qrBase(mode)}/image${query({ uid })}`),
  auth115ClearOauth: () => del<{ ok: boolean }>('auth115/oauth'),
};
