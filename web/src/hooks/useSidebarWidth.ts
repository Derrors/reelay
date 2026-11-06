import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

/** 桌面侧栏宽度：展开时可拖动，在 MIN..MAX 之间（低于 NARROW 时侧栏用紧凑排版）；拖到 COLLAPSE_AT 以下松手就收起成图标栏，
 *  收起状态下往右拖超过 COLLAPSE_AT 就展开。宽度与收起状态记在当前浏览器。 */
export const SIDEBAR = { MIN: 140, MAX: 360, DEFAULT: 232, COLLAPSED: 60, COLLAPSE_AT: 100, NARROW: 216 } as const;
const WIDTH_KEY = 'reelay.sidebar.width';
const COLLAPSED_KEY = 'reelay.sidebar.collapsed';
const KEY_STEP = 16;

const clamp = (value: number) => Math.min(SIDEBAR.MAX, Math.max(SIDEBAR.MIN, Math.round(value)));

function readWidth(): number {
  const saved = Number(localStorage.getItem(WIDTH_KEY));
  return Number.isFinite(saved) && saved > 0 ? clamp(saved) : SIDEBAR.DEFAULT;
}

export function useSidebarWidth() {
  const [width, setWidth] = useState(readWidth);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSED_KEY) === '1');
  /** 拖动中指针所在的宽度（还没松手）；null 表示没在拖。 */
  const [live, setLive] = useState<number | null>(null);
  const origin = useRef<{ x: number; start: number } | null>(null);

  useEffect(() => { localStorage.setItem(WIDTH_KEY, String(width)); }, [width]);
  useEffect(() => { localStorage.setItem(COLLAPSED_KEY, collapsed ? '1' : '0'); }, [collapsed]);

  const toggleCollapsed = useCallback(() => setCollapsed((value) => !value), []);

  // 拖动时：低于收起阈值就预览收起的样子，否则在 MIN..MAX 之间跟手
  const dragging = live !== null;
  const shownCollapsed = dragging ? live < SIDEBAR.COLLAPSE_AT : collapsed;
  const shownWidth = shownCollapsed ? SIDEBAR.COLLAPSED : dragging ? clamp(live) : width;

  const onPointerDown = (e: PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const start = collapsed ? SIDEBAR.COLLAPSED : width;
    origin.current = { x: e.clientX, start };
    setLive(start);
  };
  const onPointerMove = (e: PointerEvent<HTMLElement>) => {
    if (!origin.current) return;
    setLive(origin.current.start + e.clientX - origin.current.x);
  };
  const finish = () => {
    if (!origin.current || live === null) return;
    origin.current = null;
    if (live < SIDEBAR.COLLAPSE_AT) {
      setCollapsed(true); // 收起时保留原来的展开宽度，再展开时回到它
    } else {
      setWidth(clamp(live));
      setCollapsed(false);
    }
    setLive(null);
  };
  const onDoubleClick = () => { setWidth(SIDEBAR.DEFAULT); setCollapsed(false); };
  // 键盘：左右方向键调宽度，到最小值再往左就收起
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    if (e.key === 'ArrowRight') {
      if (collapsed) setCollapsed(false);
      else setWidth((w) => clamp(w + KEY_STEP));
    } else if (!collapsed) {
      if (width <= SIDEBAR.MIN) setCollapsed(true);
      else setWidth((w) => clamp(w - KEY_STEP));
    }
  };

  // 拖动期间全页显示调整光标、不选中文字
  useEffect(() => {
    if (!dragging) return undefined;
    const { cursor, userSelect } = document.body.style;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    return () => { document.body.style.cursor = cursor; document.body.style.userSelect = userSelect; };
  }, [dragging]);

  return {
    width: shownWidth,
    collapsed: shownCollapsed,
    dragging,
    toggleCollapsed,
    handle: {
      onPointerDown, onPointerMove, onPointerUp: finish, onPointerCancel: finish, onDoubleClick, onKeyDown,
      'aria-valuenow': shownWidth,
    },
  };
}
