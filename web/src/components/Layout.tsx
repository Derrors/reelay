import {
  ArrowUpCircle, BellRing, ChartColumn, ChevronRight, Clapperboard, FolderTree, Inbox, LayoutDashboard, LibraryBig,
  ListChecks, Loader2, LogOut, Menu, PanelLeftClose, PanelLeftOpen, Puzzle, RefreshCw, ScrollText, Search, Settings2,
  Sparkles, Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Suspense, useCallback, useState, type PointerEvent } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { toast } from 'sonner';
import { authApi } from '@/api/auth';
import { isUpgrading } from '@/api/update';
import { AppBackdrop } from '@/components/app/AppBackdrop';
import { ConfirmHost } from '@/components/app/ConfirmHost';
import { Toaster } from '@/components/ui/sonner';
import { Sheet } from '@/components/app/Sheet';
import { ThemeToggle } from '@/components/app/ThemeToggle';
import { CommandMenu } from '@/components/CommandMenu';
import { isMac, useCommandShortcut } from '@/hooks/useCommandShortcut';
import { useAsync } from '@/hooks/useAsync';
import { useDashboard } from '@/hooks/useDashboard';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { SIDEBAR, useSidebarWidth } from '@/hooks/useSidebarWidth';
import { usePullToRefresh } from '@/hooks/usePullToRefresh';
import { UPDATE_PATH, useUpdateStatus } from '@/hooks/useUpdateStatus';
import type { IUpdateStatus } from '@/lib/types';
import { cn, errorMessage } from '@/lib/utils';

type BadgeKey = 'pending' | 'failed';

interface NavEntry {
  path: string;
  label: string;
  icon: LucideIcon;
  badge?: BadgeKey;
  /** 命令搜索的额外匹配词。 */
  keywords?: string;
}

const PRIMARY: NavEntry[] = [
  { path: '/dashboard', label: '总览', icon: LayoutDashboard, keywords: 'dashboard home' },
  { path: '/inbox', label: '待审批', icon: Inbox, badge: 'pending', keywords: 'inbox approve 审批 确认' },
  { path: '/tasks', label: '任务', icon: ListChecks, badge: 'failed', keywords: 'tasks 失败' },
];
const MEDIA: NavEntry[] = [
  { path: '/library', label: '媒体库', icon: LibraryBig, keywords: 'library 入库' },
  { path: '/subscriptions', label: '追剧订阅', icon: BellRing, keywords: 'subscriptions 订阅 不再接收' },
  { path: '/stats', label: '统计', icon: ChartColumn, keywords: 'stats 统计 AI 调用' },
];
const SYSTEM: NavEntry[] = [
  { path: '/config', label: '设置', icon: Settings2, keywords: 'settings config 代理 telegram 115 tmdb' },
  { path: '/extensions', label: '插件', icon: Puzzle, keywords: 'plugins extensions 扩展' },
];
const TOOLS: NavEntry[] = [
  { path: '/storage', label: '115 目录', icon: FolderTree, keywords: 'storage 网盘 目录' },
  { path: '/logs', label: '运行日志', icon: ScrollText, keywords: 'logs 日志' },
];
const COMMAND_PAGES = [...PRIMARY, ...MEDIA, ...SYSTEM, ...TOOLS,
  { path: UPDATE_PATH, label: '版本更新', icon: Sparkles, keywords: 'update upgrade 升级 新版本' }];
const MOBILE_TABS: NavEntry[] = [...PRIMARY, MEDIA[0]];

/** 侧边栏徽标、Worker 状态与版本号：与总览共用一份运行状态（见 useDashboard）。 */
function useShellStatus() {
  const { dashboard } = useDashboard();
  const counts = dashboard?.counts ?? {};
  const worker = dashboard?.runtime.worker;
  const badges: Record<BadgeKey, number> = { pending: counts.pending_approval ?? 0, failed: counts.failed ?? 0 };
  return {
    badges,
    workerOnline: Boolean(worker && worker.status === 'running' && !worker.stale),
    loaded: dashboard !== null,
    version: dashboard?.version ?? '',
    backdrop: dashboard?.backdrop ?? null,
  };
}

/** 侧栏悬停时，一块底色跟着鼠标在各项之间滑动（参照 beautifului.dev 的 Sidebar Nav）。 */
function useGlide() {
  const [rect, setRect] = useState<{ y: number; h: number; x: number; w: number } | null>(null);
  const [visible, setVisible] = useState(false);
  const onPointerMove = useCallback((e: PointerEvent<HTMLElement>) => {
    const item = (e.target as HTMLElement).closest<HTMLElement>('[data-glide]');
    if (!item) { setVisible(false); return; }
    const box = e.currentTarget.getBoundingClientRect();
    const it = item.getBoundingClientRect();
    setRect({ y: it.top - box.top + e.currentTarget.scrollTop, h: it.height, x: it.left - box.left, w: it.width });
    setVisible(true);
  }, []);
  const onPointerLeave = useCallback(() => setVisible(false), []);
  const glider = rect ? (
    <span aria-hidden className={cn(
      'pointer-events-none absolute left-0 top-0 rounded-md bg-sidebar-accent/80 transition-[transform,height,width,opacity] duration-300 ease-(--ease-out-strong)',
      visible ? 'opacity-100' : 'opacity-0',
    )} style={{ transform: `translate(${rect.x}px, ${rect.y}px)`, height: rect.h, width: rect.w }} />
  ) : null;
  return { glider, onPointerMove, onPointerLeave };
}

function CountBadge({ count, tone, dot }: { count: number; tone: BadgeKey; dot?: boolean }) {
  if (!count) return null;
  if (dot) {
    return <span className={cn('absolute right-1.5 top-1.5 size-1.5 rounded-full', tone === 'failed' ? 'bg-destructive' : 'bg-warning')} />;
  }
  return (
    <span className={cn('min-w-5 rounded-sm px-1 text-center text-[11px] font-medium leading-[18px] tabular-nums ring-1 ring-inset',
      tone === 'failed' ? 'bg-destructive/8 text-destructive ring-destructive/20' : 'bg-warning/8 text-warning ring-warning/25')}>
      {count > 99 ? '99+' : count}
    </span>
  );
}

function NavItem({ item, count, onNavigate, collapsed }: {
  item: NavEntry; count?: number; onNavigate?: () => void; collapsed?: boolean;
}) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.path}
      onClick={onNavigate}
      data-glide
      title={collapsed ? item.label : undefined}
      className={({ isActive }) => cn(
        'press relative z-10 flex items-center gap-2.5 rounded-md text-[14px] lg:text-[13px]',
        collapsed ? 'size-9 justify-center' : 'px-2.5 py-2.5 lg:py-[6px]',
        isActive ? 'bg-sidebar-accent font-medium text-foreground shadow-[inset_0_0_0_1px_var(--line)]' : 'text-sidebar-foreground hover:text-foreground',
      )}
    >
      {({ isActive }) => (
        <>
          <Icon className={cn('size-4 shrink-0', isActive ? 'text-foreground' : 'text-faint')} strokeWidth={isActive ? 2.1 : 1.8} />
          {collapsed ? null : <span className="flex-1 truncate">{item.label}</span>}
          {item.badge ? <CountBadge count={count ?? 0} tone={item.badge} dot={collapsed} /> : null}
        </>
      )}
    </NavLink>
  );
}

function NavGroup({ title, items, badges, onNavigate, collapsed }: {
  title?: string; items: NavEntry[]; badges: Record<BadgeKey, number>; onNavigate?: () => void; collapsed?: boolean;
}) {
  return (
    <div className="space-y-px">
      {title ? (collapsed
        ? <div className="mx-auto my-2.5 h-px w-5 bg-sidebar-border" />
        : <div className="px-2.5 pb-1 pt-4 text-[11px] font-medium text-faint">{title}</div>) : null}
      {items.map((item) => (
        <NavItem key={item.path} item={item} count={item.badge ? badges[item.badge] : undefined}
          onNavigate={onNavigate} collapsed={collapsed} />
      ))}
    </div>
  );
}

function ToolsGroup({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation();
  const inside = TOOLS.some((t) => location.pathname.startsWith(t.path));
  const [open, setOpen] = useState(inside);
  const expanded = open || inside;
  return (
    <div className="space-y-px">
      <button
        type="button"
        onClick={() => setOpen(!expanded)}
        aria-expanded={expanded}
        data-glide
        className="press relative z-10 flex w-full items-center gap-2.5 rounded-md px-2.5 py-[6px] text-[13px] text-sidebar-foreground hover:text-foreground"
      >
        <Wrench className="size-4 shrink-0 text-faint" strokeWidth={1.8} />
        <span className="flex-1 text-left">工具</span>
        <ChevronRight className={cn('size-3.5 text-faint transition-transform duration-300 ease-(--ease-out-strong)', expanded && 'rotate-90')} />
      </button>
      <div className={cn('grid transition-[grid-template-rows] duration-300 ease-(--ease-out-strong)', expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}>
        <div className="min-h-0 overflow-hidden">
          <div className="ml-[17px] space-y-px border-l border-sidebar-border py-px pl-2">
            {TOOLS.map((item) => <NavItem key={item.path} item={item} onNavigate={onNavigate} />)}
          </div>
        </div>
      </div>
    </div>
  );
}

function Brand({ compact }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-foreground text-background shadow-[0_1px_2px_oklch(0%_0_0/0.2),inset_0_1px_0_oklch(100%_0_0/0.15)]">
        <Clapperboard className="size-4" />
      </div>
      {compact ? null : (
        <div className="leading-tight">
          <div className="text-[14px] font-semibold tracking-[-0.01em] text-foreground">Reelay</div>
          <div className="text-[11px] text-faint">网盘转存助手</div>
        </div>
      )}
    </div>
  );
}

function SearchButton({ onOpen, collapsed, narrow }: { onOpen: () => void; collapsed?: boolean; narrow?: boolean }) {
  return (
    <button type="button" onClick={onOpen} title={collapsed ? `搜索（${isMac ? '⌘' : 'Ctrl+'}K）` : undefined}
      className={cn('press flex items-center gap-2 rounded-md bg-card text-[13px] text-faint shadow-btn hover:text-muted-foreground',
        collapsed ? 'mx-auto size-9 justify-center' : 'w-full px-2.5 py-1.5')}>
      <Search className="size-3.5 shrink-0" />
      {collapsed ? null : (
        <>
          <span className="flex-1 text-left">搜索</span>
          {narrow ? null : <kbd className="rounded-sm bg-muted px-1 font-mono text-[10px] text-muted-foreground">{isMac ? '⌘' : 'Ctrl'} K</kbd>}
        </>
      )}
    </button>
  );
}

function VersionLine({ version, update, onNavigate, centered }: {
  version: string; update?: IUpdateStatus; onNavigate?: () => void; centered?: boolean;
}) {
  return (
    <span className={cn('mt-0.5 flex items-center gap-1.5', centered ? 'justify-center' : 'pl-3.5')}>
      {/* 反馈问题时需要附上版本号：可选中复制 */}
      <span className="select-all font-mono text-[11px] text-faint" title="当前版本，反馈问题时请附上">
        v{version.replace(/^v/, '')}
      </span>
      {update?.available ? null : (
        <NavLink to={UPDATE_PATH} onClick={onNavigate} title="检查更新" aria-label="检查更新"
          className="grid size-4 place-items-center rounded text-faint transition-colors hover:text-foreground">
          <RefreshCw className="size-3" />
        </NavLink>
      )}
    </span>
  );
}

function UpdateEntry({ update, onNavigate, collapsed, narrow }: {
  update?: IUpdateStatus; onNavigate?: () => void; collapsed?: boolean; narrow?: boolean;
}) {
  if (!update?.available && !isUpgrading(update)) return null;
  const busy = isUpgrading(update);
  const Icon = busy ? Loader2 : ArrowUpCircle;
  const text = busy ? `正在升级到 v${update?.upgrade.state?.target}…` : `新版本 v${update?.latest} 可升级`;
  const short = busy ? '升级中…' : `v${update?.latest} 可升级`;
  return (
    <NavLink to={UPDATE_PATH} onClick={onNavigate} title={collapsed || narrow ? text : '查看更新说明与升级方法'}
      className={cn('press flex min-h-9 items-center gap-2 rounded-md bg-primary/8 text-xs font-medium text-primary ring-1 ring-inset ring-primary/15 hover:bg-primary/12 sm:min-h-0',
        collapsed ? 'mx-auto size-9 justify-center' : 'w-full px-2.5 py-1.5')}>
      <Icon className={cn('size-3.5 shrink-0', busy && 'animate-spin')} />
      {collapsed ? null : <span className="flex-1 truncate text-left">{narrow ? short : text}</span>}
    </NavLink>
  );
}

function ShellFooter({ workerOnline, loaded, version, update, onNavigate, collapsed, narrow }: {
  workerOnline: boolean; loaded: boolean; version: string; update?: IUpdateStatus; onNavigate?: () => void; collapsed?: boolean;
  /** 侧栏拖得较窄：状态与主题切换上下排列。 */
  narrow?: boolean;
}) {
  const auth = useAsync(() => authApi.status(), []);
  const logout = async () => {
    try {
      await authApi.logout();
      window.location.reload();
    } catch (e) {
      toast.error(errorMessage(e, '退出登录失败'));
    }
  };
  const state = !loaded ? '连接中' : workerOnline ? '后台运行中' : '后台未运行';
  const dot = <span className={cn('size-2 shrink-0 rounded-full', !loaded ? 'bg-faint' : workerOnline ? 'bg-success shadow-[0_0_0_3px_color-mix(in_oklab,var(--success)_18%,transparent)]' : 'bg-warning')} />;
  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-2 border-t border-sidebar-border px-2 py-3">
        <UpdateEntry update={update} collapsed />
        <span className="grid size-9 place-items-center" title={`${state}${version ? ` · v${version.replace(/^v/, '')}` : ''}`}>{dot}</span>
        {auth.data?.auth_required ? (
          <button type="button" onClick={() => void logout()} title="退出登录"
            className="press grid size-9 place-items-center rounded-md text-faint hover:bg-sidebar-accent hover:text-foreground">
            <LogOut className="size-4" />
          </button>
        ) : null}
      </div>
    );
  }
  return (
    <div className="space-y-3 border-t border-sidebar-border px-3 py-3">
      <UpdateEntry update={update} onNavigate={onNavigate} narrow={narrow} />
      <div className={cn('flex gap-2 px-1', narrow ? 'flex-col' : 'items-center justify-between')}>
        <span className={cn('flex min-w-0 flex-col text-xs text-sidebar-foreground', narrow && 'items-center')}>
          <span className="flex min-w-0 items-center gap-1.5">{dot}<span className="truncate">{state}</span></span>
          {version ? <VersionLine version={version} update={update} onNavigate={onNavigate} centered={narrow} /> : null}
        </span>
        <ThemeToggle fill={narrow} className={narrow ? 'w-full' : undefined} />
      </div>
      {auth.data?.auth_required ? (
        <button
          type="button"
          onClick={() => void logout()}
          className="press flex min-h-9 w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-sidebar-foreground hover:bg-sidebar-accent hover:text-foreground sm:min-h-0"
        >
          <LogOut className="size-3.5" />退出登录
        </button>
      ) : null}
    </div>
  );
}

function NavContent({ badges, onNavigate, mobile, collapsed }: {
  badges: Record<BadgeKey, number>; onNavigate?: () => void; mobile?: boolean; collapsed?: boolean;
}) {
  const { glider, onPointerMove, onPointerLeave } = useGlide();
  if (mobile) {
    // 底部标签栏已有总览、待审批、任务、媒体库；抽屉只放其余入口，工具不再折叠
    const tabs = new Set(MOBILE_TABS.map((t) => t.path));
    return (
      <nav aria-label="主导航" className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <NavGroup title="媒体" items={MEDIA.filter((t) => !tabs.has(t.path))} badges={badges} onNavigate={onNavigate} />
        <NavGroup title="系统" items={SYSTEM} badges={badges} onNavigate={onNavigate} />
        <NavGroup title="工具" items={TOOLS} badges={badges} onNavigate={onNavigate} />
      </nav>
    );
  }
  return (
    <nav aria-label="主导航" onPointerMove={onPointerMove} onPointerLeave={onPointerLeave}
      className={cn('relative min-h-0 flex-1 overflow-y-auto pb-4', collapsed ? 'flex flex-col items-center px-2' : 'px-3')}>
      {glider}
      <NavGroup items={PRIMARY} badges={badges} onNavigate={onNavigate} collapsed={collapsed} />
      <NavGroup title="媒体" items={MEDIA} badges={badges} onNavigate={onNavigate} collapsed={collapsed} />
      <NavGroup title="系统" items={SYSTEM} badges={badges} onNavigate={onNavigate} collapsed={collapsed} />
      {collapsed
        ? <NavGroup title="工具" items={TOOLS} badges={badges} onNavigate={onNavigate} collapsed />
        : <div className="mt-px"><ToolsGroup onNavigate={onNavigate} /></div>}
    </nav>
  );
}

export const Layout = () => {
  const status = useShellStatus();
  const [drawer, setDrawer] = useState(false);
  const [command, setCommand] = useState(false);
  const sidebar = useSidebarWidth();
  const { collapsed, toggleCollapsed } = sidebar;
  const narrow = !collapsed && sidebar.width < SIDEBAR.NARROW;
  const openCommand = useCallback(() => { setDrawer(false); setCommand(true); }, []);
  useCommandShortcut(openCommand);
  const update = useUpdateStatus();
  const { pathname } = useLocation();
  const tabIndex = MOBILE_TABS.findIndex((t) => pathname.startsWith(t.path));
  const pullEnabled = useMediaQuery('(max-width: 1023px)');
  const { pull, refreshing, ready, threshold } = usePullToRefresh(pullEnabled);
  const footer = { workerOnline: status.workerOnline, loaded: status.loaded, version: status.version, update: update.data };

  return (
    <div style={{ '--sidebar-width': `${sidebar.width}px` } as React.CSSProperties}
      className={cn('min-h-screen text-foreground lg:grid lg:grid-cols-[var(--sidebar-width)_minmax(0,1fr)]',
        // 拖动时跟手，不做过渡；点按钮收起展开、松手吸附时才有动画
        !sidebar.dragging && 'lg:transition-[grid-template-columns] lg:duration-300 lg:ease-(--ease-out-strong)')}>
      <AppBackdrop path={status.backdrop} />
      {/* 桌面侧边栏 */}
      <aside className="glass-page glass-blur relative hidden overflow-hidden border-r border-sidebar-border lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
        <div className={cn('flex items-center pb-3 pt-5', collapsed ? 'flex-col gap-3 px-2' : 'justify-between px-4')}>
          <Brand compact={collapsed || narrow} />
          <button type="button" onClick={toggleCollapsed} title={collapsed ? '展开侧栏' : '收起侧栏'} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
            className="press grid size-8 place-items-center rounded-md text-faint hover:bg-sidebar-accent hover:text-foreground">
            {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          </button>
        </div>
        <div className={cn('pb-2', collapsed ? 'px-2' : 'px-3')}><SearchButton onOpen={openCommand} collapsed={collapsed} narrow={narrow} /></div>
        <NavContent badges={status.badges} collapsed={collapsed} />
        <ShellFooter {...footer} collapsed={collapsed} narrow={narrow} />
        {/* 拖动调整宽度：拖到很窄时松手收起，双击恢复默认宽度 */}
        <div role="separator" aria-orientation="vertical" aria-label="调整侧栏宽度" tabIndex={0}
          aria-valuemin={SIDEBAR.COLLAPSED} aria-valuemax={SIDEBAR.MAX}
          title="拖动调整宽度，拖到最左收起；双击恢复默认"
          {...sidebar.handle}
          className="group/handle absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none outline-none">
          <span className={cn('absolute inset-y-0 right-0 w-0.5 transition-colors duration-150',
            sidebar.dragging ? 'bg-primary' : 'bg-transparent group-hover/handle:bg-primary/50 group-focus-visible/handle:bg-primary')} />
        </div>
      </aside>

      {/* 移动端顶栏 */}
      <header className="sticky top-0 z-30 flex h-[calc(3.5rem+env(safe-area-inset-top))] items-center justify-between border-b glass-page glass-blur px-4 pt-[env(safe-area-inset-top)] lg:hidden">
        <Brand />
        <div className="flex items-center gap-1">
          <button type="button" onClick={openCommand} aria-label="搜索"
            className="press grid size-9 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
            <Search className="size-[18px]" />
          </button>
          <button type="button" onClick={() => setDrawer(true)} aria-label="打开导航"
            className="press grid size-9 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
            <Menu className="size-5" />
          </button>
        </div>
      </header>
      <Sheet open={drawer} onOpenChange={setDrawer} title="导航" side="left" className="p-0">
        <div className="-mx-5 -my-5 flex h-[calc(100%+2.5rem)] flex-col">
          <NavContent badges={status.badges} onNavigate={() => setDrawer(false)} mobile />
          <ShellFooter {...footer} onNavigate={() => setDrawer(false)} />
        </div>
      </Sheet>

      <main className="min-w-0">
        <Suspense fallback={null}><Outlet context={update} /></Suspense>
      </main>

      {/* 下拉刷新指示器（手机） */}
      {pullEnabled ? (
        <div aria-hidden className="pointer-events-none fixed inset-x-0 top-[calc(3.5rem+env(safe-area-inset-top))] z-20 flex justify-center lg:hidden"
          style={{ transform: `translateY(${refreshing ? 14 : pull * 0.6 - 26}px)`, opacity: refreshing ? 1 : Math.min(1, pull / threshold),
            transition: pull ? 'none' : 'transform 300ms var(--ease-out-strong), opacity 300ms' }}>
          <span className="grid size-9 place-items-center rounded-full bg-card shadow-raised">
            <RefreshCw className={cn('size-4', ready || refreshing ? 'text-primary' : 'text-muted-foreground', refreshing && 'animate-spin')}
              style={refreshing ? undefined : { transform: `rotate(${pull * 3}deg)` }} />
          </span>
        </div>
      ) : null}

      {/* 移动端浮动 Dock：选中底块随标签滑动（参照 MoviePilot） */}
      <nav aria-label="快捷导航" className="fixed inset-x-3 bottom-[calc(0.5rem+env(safe-area-inset-bottom))] z-30 grid grid-cols-5 rounded-2xl glass-page glass-blur p-1.5 shadow-overlay lg:hidden">
        {tabIndex >= 0 ? (
          <span aria-hidden className="absolute inset-y-1.5 left-1.5 w-[calc((100%-0.75rem)/5)] rounded-xl bg-muted shadow-[inset_0_0_0_1px_var(--line)] transition-transform duration-300 ease-(--ease-out-strong)"
            style={{ transform: `translateX(${tabIndex * 100}%)` }} />
        ) : null}
        {MOBILE_TABS.map((item) => {
          const Icon = item.icon;
          const count = item.badge ? status.badges[item.badge] : 0;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => cn(
                'press relative flex flex-col items-center gap-0.5 rounded-xl py-1.5 text-[11px] font-medium',
                isActive ? 'text-foreground' : 'text-faint',
              )}
            >
              {({ isActive }) => (
                <>
                  <Icon className="size-5" strokeWidth={isActive ? 2.1 : 1.8} />
                  {item.label}
                  {count ? (
                    <span className={cn(
                      'absolute left-1/2 top-0.5 ml-1.5 min-w-4 rounded-full px-1 text-center text-[10px] font-semibold leading-4 text-white',
                      item.badge === 'failed' ? 'bg-destructive' : 'bg-warning',
                    )}>
                      {count > 99 ? '99+' : count}
                    </span>
                  ) : null}
                </>
              )}
            </NavLink>
          );
        })}
        <button
          type="button"
          onClick={() => setDrawer(true)}
          className="press relative flex flex-col items-center gap-0.5 rounded-xl py-1.5 text-[11px] font-medium text-faint"
          aria-label={update.data?.available ? '更多（有新版本）' : undefined}
        >
          <Menu className="size-5" strokeWidth={1.8} />
          更多
          {update.data?.available ? <span className="absolute left-1/2 top-1 ml-2 size-2 rounded-full bg-primary" /> : null}
        </button>
      </nav>

      <CommandMenu open={command} onOpenChange={setCommand} pages={COMMAND_PAGES} />
      <Toaster position="top-right" closeButton />
      <ConfirmHost />
    </div>
  );
};
