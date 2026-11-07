import { useEffect } from 'react';
import { Realtime, type ChangeScope } from '@/lib/realtime';

/** 这些范围有变化时重新取数；scopes 请用模块级常量，保持引用稳定。 */
export function useLiveReload(scopes: ChangeScope[], reload: () => void) {
  useEffect(() => {
    const realtime = new Realtime({ scopes, onChange: reload });
    realtime.start();
    return () => realtime.stop();
  }, [scopes, reload]);
}
