/** 命令搜索（⌘K / Ctrl+K）：输入即过滤，跳转页面、切换主题、按片名搜索任务。上下键选择，回车执行。 */
import * as DialogPrimitive from '@radix-ui/react-dialog';
import type { LucideIcon } from 'lucide-react';
import { CornerDownLeft, Layers, Monitor, Moon, Search, Sun } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { isMac } from '@/hooks/useCommandShortcut';
import { useGlass } from '@/hooks/useGlass';
import { setGlass } from '@/lib/appearance';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';

export interface CommandEntry {
  id: string;
  label: string;
  group: string;
  icon: LucideIcon;
  /** 额外的匹配词（拼音首字母、英文名等）。 */
  keywords?: string;
  run: () => void;
}

export function CommandMenu({ open, onOpenChange, pages }: {
  open: boolean; onOpenChange: (open: boolean) => void;
  pages: Array<{ path: string; label: string; icon: LucideIcon; keywords?: string }>;
}) {
  const navigate = useNavigate();
  const { setTheme } = useTheme();
  const glass = useGlass().enabled;
  const [text, setText] = useState('');
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const close = () => { setText(''); setActive(0); onOpenChange(false); };

  const entries = useMemo<CommandEntry[]>(() => {
    const go = (path: string) => () => { navigate(path); close(); };
    const theme = (value: string) => () => { setTheme(value); close(); };
    return [
      ...pages.map((page) => ({ id: page.path, label: page.label, group: '页面', icon: page.icon,
        keywords: `${page.path} ${page.keywords ?? ''}`, run: go(page.path) })),
      { id: 'theme-light', label: '切换到浅色主题', group: '外观', icon: Sun, keywords: 'light theme 主题', run: theme('light') },
      { id: 'theme-dark', label: '切换到深色主题', group: '外观', icon: Moon, keywords: 'dark theme 主题', run: theme('dark') },
      { id: 'theme-system', label: '主题跟随系统', group: '外观', icon: Monitor, keywords: 'system theme 主题', run: theme('system') },
      { id: 'glass', label: glass ? '关闭毛玻璃效果' : '开启毛玻璃效果', group: '外观', icon: Layers,
        keywords: 'glass blur 透明 背景 剧照', run: () => { setGlass(!glass); close(); } },
    ];
    // close 只调用 onOpenChange，不需要随它重建
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, navigate, setTheme, glass]);

  const query = text.trim().toLowerCase();
  const shown = useMemo(() => {
    const matched = query
      ? entries.filter((e) => `${e.label} ${e.keywords ?? ''}`.toLowerCase().includes(query))
      : entries;
    if (!query) return matched;
    // 有输入时总能按片名搜任务
    return [...matched, {
      id: 'search-tasks', label: `在任务里搜索「${text.trim()}」`, group: '任务', icon: Search,
      run: () => { navigate(`/tasks?q=${encodeURIComponent(text.trim())}`); close(); },
    }];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, query, text, navigate]);

  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); shown[active]?.run(); }
  };

  let lastGroup = '';
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/15 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 dark:bg-black/45" />
        <DialogPrimitive.Content
          onKeyDown={onKeyDown}
          className="fixed left-1/2 top-[14vh] z-50 w-[calc(100%-2rem)] max-w-[560px] -translate-x-1/2 overflow-hidden rounded-xl glass-overlay glass-blur shadow-overlay outline-none duration-200 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.97] data-[state=open]:ease-(--ease-out-strong) data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-[0.97]"
        >
          <DialogPrimitive.Title className="sr-only">命令搜索</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">输入页面名称、操作或片名</DialogPrimitive.Description>
          <div className="flex items-center gap-2.5 border-b px-4">
            <Search className="size-4 shrink-0 text-faint" />
            <input
              autoFocus
              value={text}
              onChange={(e) => { setText(e.target.value); setActive(0); }}
              placeholder="搜索页面、操作，或输入片名搜索任务"
              className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
              role="combobox"
              aria-expanded
              aria-controls="command-list"
              aria-activedescendant={shown[active] ? `command-${shown[active].id}` : undefined}
            />
            <kbd className="hidden rounded border bg-muted px-1.5 font-mono text-[10px] text-muted-foreground sm:inline">Esc</kbd>
          </div>
          <div ref={list} id="command-list" role="listbox" className="max-h-[min(60vh,380px)] overflow-y-auto p-1.5">
            {shown.length ? shown.map((entry, index) => {
              const header = entry.group !== lastGroup ? entry.group : null;
              lastGroup = entry.group;
              const Icon = entry.icon;
              return (
                <div key={entry.id}>
                  {header ? <div className="px-2.5 pb-1 pt-2 text-[11px] font-medium text-faint">{header}</div> : null}
                  <button
                    type="button"
                    id={`command-${entry.id}`}
                    role="option"
                    aria-selected={index === active}
                    data-index={index}
                    onMouseMove={() => setActive(index)}
                    onClick={entry.run}
                    className={cn('flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] transition-colors duration-100',
                      index === active ? 'bg-accent text-foreground' : 'text-foreground/85')}
                  >
                    <Icon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate">{entry.label}</span>
                    {index === active ? <CornerDownLeft className="size-3.5 text-faint" /> : null}
                  </button>
                </div>
              );
            }) : <p className="px-3 py-8 text-center text-sm text-muted-foreground">没有匹配的结果</p>}
          </div>
          <div className="flex items-center gap-3 border-t px-4 py-2 text-[11px] text-faint">
            <span><kbd className="font-mono">↑↓</kbd> 选择</span>
            <span><kbd className="font-mono">↵</kbd> 打开</span>
            <span className="ml-auto">{isMac ? '⌘' : 'Ctrl'} K 随时打开</span>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
