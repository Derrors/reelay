import { useEffect, useState } from 'react';

/** 输入停下 delay 毫秒后才更新的值（搜索框防抖）；返回去掉首尾空白的结果。 */
export function useDebounced(value: string, delay = 300): string {
  const [settled, setSettled] = useState(value.trim());
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value.trim()), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
