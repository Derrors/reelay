/** 手动刷新（手机下拉刷新）：通知当前页面上的取数（useAsync）与实时订阅（Realtime）各重取一次。 */
const EVENT = 'reelay:refresh';

export function requestRefresh() {
  window.dispatchEvent(new Event(EVENT));
}

export function onRefreshRequest(listener: () => void): () => void {
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
