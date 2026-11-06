/** 界面外观里只保存在当前浏览器的偏好：毛玻璃效果（默认开）。
 *  写在 <html data-glass="on|off">，CSS 据此切换半透明与背景剧照（见 tailwind-theme.css）。 */
const KEY = 'reelay.glass';
const listeners = new Set<() => void>();

export function glassEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) !== 'off';
  } catch {
    return true;
  }
}

/** 页面渲染前调用一次，避免先闪一下实色。 */
export function applyGlass() {
  document.documentElement.dataset.glass = glassEnabled() ? 'on' : 'off';
}

export function setGlass(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch { /* 无痕模式等存不了时只对本次打开生效 */ }
  document.documentElement.dataset.glass = on ? 'on' : 'off';
  listeners.forEach((listener) => listener());
}

export function subscribeGlass(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
