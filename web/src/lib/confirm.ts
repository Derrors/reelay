/** 页面内确认弹窗（替代浏览器自带的 window.confirm）：`if (!(await confirmDialog({...}))) return;`
 *  弹窗由 Layout 里的 ConfirmHost 渲染；同一时间只有一个，新的请求会把旧的当作取消。 */

export interface ConfirmOptions {
  title: string;
  description?: string;
  /** 涉及的对象（如文件名），逐行等宽显示，方便核对。 */
  details?: Array<{ label: string; value: string }>;
  confirmText?: string;
  cancelText?: string;
  /** 删除、清空这类不可逆或会丢东西的操作：确认按钮用警示色。 */
  destructive?: boolean;
}

export interface ConfirmRequest extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

type Listener = (request: ConfirmRequest | null) => void;

let current: ConfirmRequest | null = null;
const listeners = new Set<Listener>();

function publish(request: ConfirmRequest | null) {
  current = request;
  listeners.forEach((listener) => listener(request));
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  current?.resolve(false);
  return new Promise((resolve) => {
    publish({
      ...options,
      resolve: (ok) => {
        resolve(ok);
        publish(null);
      },
    });
  });
}

export function subscribeConfirm(listener: Listener): () => void {
  listeners.add(listener);
  listener(current);
  return () => { listeners.delete(listener); };
}
