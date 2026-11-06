/** 检查更新的结果：整个界面共用一份（Layout 取数，页面经 Outlet context 读取）。
 *  发现新版本时在右上角提示一次；一键升级期间轮询进度，完成后刷新页面换成新版本的管理台。 */
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate, useOutletContext } from 'react-router-dom';
import { toast } from 'sonner';
import { isUpgrading, updateApi } from '@/api/update';
import { useAsync, type AsyncResult } from '@/hooks/useAsync';
import type { IUpdateStatus } from '@/lib/types';

export const UPDATE_PATH = '/update';
const RELOAD_MS = 3600_000; // 页面长时间开着时也能看到新版本；服务端缓存 6 小时，不会频繁联网
const UPGRADE_POLL_MS = 2000; // 一键升级期间查看进度；管理服务重启时连不上，继续等
const NOTICED_KEY = 'reelay.update.noticed'; // 这个浏览器已经提示过的版本
const NOTICE_MS = 12_000;

export type UpdateState = AsyncResult<IUpdateStatus>;

export function useUpdateStatus(): UpdateState {
  const update = useAsync(() => updateApi.get(), []);
  const { reload, mutate, data } = update;
  const busy = isUpgrading(data);
  const state = data?.upgrade?.state;
  const watched = useRef(false); // 这个页面看着一次升级进行过
  const navigate = useNavigate();
  const { pathname } = useLocation();

  useEffect(() => {
    const timer = window.setInterval(reload, RELOAD_MS);
    return () => window.clearInterval(timer);
  }, [reload]);

  // 右上角提示一次：每个新版本在每个浏览器里只提示一次，不打断操作
  const latest = data?.enabled && data.available && !busy ? data.latest : null;
  useEffect(() => {
    if (!latest || localStorage.getItem(NOTICED_KEY) === latest) return;
    localStorage.setItem(NOTICED_KEY, latest);
    if (pathname === UPDATE_PATH) return; // 已经在看了
    toast.info(`Reelay v${latest} 已发布`, {
      description: '可以在侧栏底部或「版本更新」页查看更新说明与升级方法。',
      duration: NOTICE_MS,
      action: { label: '查看', onClick: () => navigate(UPDATE_PATH) },
    });
  }, [latest, pathname, navigate]);

  useEffect(() => {
    if (!busy) return undefined;
    watched.current = true;
    let stopped = false;
    const timer = window.setInterval(() => {
      updateApi.get().then((next) => { if (!stopped) mutate(() => next); }, () => undefined);
    }, UPGRADE_POLL_MS);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [busy, mutate]);

  useEffect(() => {
    if (!watched.current) return;
    if (state?.status === 'done') {
      window.location.reload(); // 换成新版本的管理台
    } else if (state?.status === 'failed') {
      watched.current = false;
      toast.error(`升级失败：${state.error ?? '原因见升级日志'}`);
    }
  }, [state?.status, state?.error]);
  return update;
}

/** 页面里读取 Layout 取到的检查更新结果。 */
export const useUpdate = () => useOutletContext<UpdateState>();
