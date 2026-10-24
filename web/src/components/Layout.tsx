import {
  BellRing, ChartColumn, ChevronRight, Clapperboard, FolderTree, Inbox, LayoutDashboard, LibraryBig,
  ListChecks, LogOut, Menu, Puzzle, ScrollText, Settings2, Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Suspense, useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { ConfirmHost } from '@/components/app/ConfirmHost';
import { Toaster } from '@/components/ui/sonner';
import { Sheet } from '@/components/app/Sheet';
import { ThemeToggle } from '@/components/app/ThemeToggle';
import { api } from '@/api/client';
import { extras } from '@/api/extras';
import { Realtime } from '@/lib/realtime';
import type { IDashboard } from '@/lib/types';
import { cn } from '@/lib/utils';

type BadgeKey = 'pending' | 'failed';

interface NavEntry {
  path: string;
  label: string;
  icon: LucideIcon;
  badge?: BadgeKey;
}

const PRIMARY: NavEntry[] = [
  { path: '/dashboard', label: '总览', icon: LayoutDashboard },
  { path: '/inbox', label: '待审批', icon: Inbox, badge: 'pending' },
  { path: '/tasks', label: '任务', icon: ListChecks, badge: 'failed' },
];
const MEDIA: NavEntry[] = [
  { path: '/library', label: '媒体库', icon: LibraryBig },
  { path: '/subscriptions', label: '追剧订阅', icon: BellRing },
  { path: '/stats', label: '统计', icon: ChartColumn },
];
const SYSTEM: NavEntry[] = [
  { path: '/config', label: '设置', icon: Settings2 },
  { path: '/extensions', label: '插件', icon: Puzzle },
];
const TOOLS: NavEntry[] = [
  { path: '/storage', label: '115 目录', icon: FolderTree },
  { path: '/logs', label: '运行日志', icon: ScrollText },
];
const MOBILE_TABS: NavEntry[] = [...PRIMARY, MEDIA[0]];

/** 侧边栏徽标与 Worker 状态：订阅实时通道，计数变化时刷新。 */
function useShellStatus() {
  const [dashboard, setDashboard] = useState<IDashboard | null>(null);
  useEffect(() => {
    let active = true;
    const load = () => api.dashboard().then((d) => { if (active) setDashboard(d); }).catch(() => undefined);
    void load();
    const realtime = new Realtime({ scopes: ['counts', 'runtime'], onChange: () => { void load(); } });
    realtime.start();
    return () => { active = false; realtime.stop(); };
  }, []);
  const counts = dashboard?.counts ?? {};
  const worker = dashboard?.runtime.worker;
  return {
    badges: { pending: counts.pending_approval ?? 0, failed: counts.failed ?? 0 } as Record<BadgeKey, number>,
    workerOnline: Boolean(worker && worker.status === 'running' && !worker.stale),
    alerts: dashboard?.alerts?.length ?? 0,
    loaded: dashboard !== null,
  };
}

function NavItem({ item, count, onNavigate }: { item: NavEntry; count?: number; onNavigate?: () => void }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.path}
      onClick={onNavigate}
      className={({ isActive }) => cn(
        'group flex items-center gap-2.5 rounded-lg px-2.5 py-2.5 text-[14px] font-medium transition-colors lg:py-[7px] lg:text-[13.5px]',
        isActive
          ? 'bg-sidebar-accent text-sidebar-accent-foreground shadow-xs ring-1 ring-sidebar-border'
          : 'text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground',
      )}
    >
      {({ isActive }) => (
        <>
          <Icon className={cn('size-4 shrink-0', isActive ? 'text-sidebar-primary' : 'text-sidebar-foreground/70')} />
          <span className="flex-1 truncate">{item.label}</span>
          {count ? (
            <span className={cn(
              'min-w-5 rounded-full px-1.5 text-center text-[11px] font-semibold leading-5 tabular-nums',
              item.badge === 'failed' ? 'bg-destructive/12 text-destructive' : 'bg-warning/15 text-warning',
            )}>
              {count > 99 ? '99+' : count}
            </span>
          ) : null}
        </>
      )}
    </NavLink>
  );
}

function NavGroup({ title, items, badges, onNavigate }: {
  title?: string; items: NavEntry[]; badges: Record<BadgeKey, number>; onNavigate?: () => void;
}) {
  return (
    <div className="space-y-0.5">
      {title ? <div className="px-2.5 pb-1 pt-4 text-[11px] font-medium tracking-wide text-sidebar-foreground/55">{title}</div> : null}
      {items.map((item) => (
        <NavItem key={item.path} item={item} count={item.badge ? badges[item.badge] : undefined} onNavigate={onNavigate} />
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
    <div className="space-y-0.5">
      <button
        type="button"
        onClick={() => setOpen(!expanded)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium text-sidebar-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
      >
        <Wrench className="size-4 shrink-0 text-sidebar-foreground/70" />
        <span className="flex-1 text-left">工具</span>
        <ChevronRight className={cn('size-3.5 text-sidebar-foreground/50 transition-transform', expanded && 'rotate-90')} />
      </button>
      {expanded ? (
        <div className="ml-[17px] space-y-0.5 border-l border-sidebar-border pl-2">
          {TOOLS.map((item) => <NavItem key={item.path} item={item} onNavigate={onNavigate} />)}
        </div>
      ) : null}
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-8 place-items-center rounded-[9px] bg-gradient-to-br from-[hsl(171_58%_44%)] to-[hsl(178_64%_26%)] text-white shadow-[0_4px_12px_-3px_hsl(173_60%_30%/0.6)]">
        <Clapperboard className="size-4" />
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-foreground">Reelay</div>
        <div className="text-[11px] text-muted-foreground">网盘转存助手</div>
      </div>
    </div>
  );
}

function ShellFooter({ workerOnline, loaded }: { workerOnline: boolean; loaded: boolean }) {
  const [authEnabled, setAuthEnabled] = useState(false);
  useEffect(() => {
    extras.authStatus().then((r) => setAuthEnabled(r.auth_required)).catch(() => undefined);
  }, []);
  return (
    <div className="space-y-3 border-t border-sidebar-border px-3 py-3">
      <div className="flex items-center justify-between gap-2 px-1">
        <span className="flex items-center gap-2 text-xs text-sidebar-foreground">
          <span className={cn('size-2 rounded-full', !loaded ? 'bg-muted-foreground/40' : workerOnline ? 'bg-success' : 'bg-warning')} />
          {!loaded ? '连接中' : workerOnline ? '后台运行中' : '后台未运行'}
        </span>
        <ThemeToggle />
      </div>
      {authEnabled ? (
        <button
          type="button"
          onClick={() => { void extras.logout().then(() => window.location.reload()); }}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium text-sidebar-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
        >
          <LogOut className="size-3.5" />退出登录
        </button>
      ) : null}
    </div>
  );
}

function NavContent({ badges, onNavigate, mobile }: {
  badges: Record<BadgeKey, number>; onNavigate?: () => void; mobile?: boolean;
}) {
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
    <nav aria-label="主导航" className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
      <NavGroup items={PRIMARY} badges={badges} onNavigate={onNavigate} />
      <NavGroup title="媒体" items={MEDIA} badges={badges} onNavigate={onNavigate} />
      <NavGroup title="系统" items={SYSTEM} badges={badges} onNavigate={onNavigate} />
      <div className="mt-0.5"><ToolsGroup onNavigate={onNavigate} /></div>
    </nav>
  );
}

export const Layout = () => {
  const status = useShellStatus();
  const [drawer, setDrawer] = useState(false);

  return (
    <div className="min-h-screen text-foreground lg:grid lg:grid-cols-[232px_minmax(0,1fr)]">
      {/* 桌面侧边栏 */}
      <aside className="hidden border-r border-sidebar-border bg-sidebar lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
        <div className="px-5 pb-3 pt-5"><Brand /></div>
        <NavContent badges={status.badges} />
        <ShellFooter workerOnline={status.workerOnline} loaded={status.loaded} />
      </aside>

      {/* 移动端顶栏 */}
      <header className="sticky top-0 z-30 flex h-[calc(3.5rem+env(safe-area-inset-top))] items-center justify-between border-b bg-background/85 px-4 pt-[env(safe-area-inset-top)] backdrop-blur lg:hidden">
        <Brand />
        <button
          type="button"
          onClick={() => setDrawer(true)}
          className="grid size-9 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label="打开导航"
        >
          <Menu className="size-5" />
        </button>
      </header>
      <Sheet open={drawer} onOpenChange={setDrawer} title="导航" side="left" className="bg-sidebar p-0">
        <div className="-mx-5 -my-5 flex h-[calc(100%+2.5rem)] flex-col">
          <NavContent badges={status.badges} onNavigate={() => setDrawer(false)} mobile />
          <ShellFooter workerOnline={status.workerOnline} loaded={status.loaded} />
        </div>
      </Sheet>

      <main className="min-w-0">
        <Suspense fallback={null}><Outlet /></Suspense>
      </main>

      {/* 移动端底部标签栏 */}
      <nav aria-label="快捷导航" className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t bg-background/92 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        {MOBILE_TABS.map((item) => {
          const Icon = item.icon;
          const count = item.badge ? status.badges[item.badge] : 0;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => cn(
                'relative flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium',
                isActive ? 'text-primary' : 'text-muted-foreground',
              )}
            >
              <Icon className="size-5" />
              {item.label}
              {count ? (
                <span className={cn(
                  'absolute left-1/2 top-1 ml-1.5 min-w-4 rounded-full px-1 text-center text-[10px] font-semibold leading-4 text-white',
                  item.badge === 'failed' ? 'bg-destructive' : 'bg-warning',
                )}>
                  {count > 99 ? '99+' : count}
                </span>
              ) : null}
            </NavLink>
          );
        })}
        <button
          type="button"
          onClick={() => setDrawer(true)}
          className="flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium text-muted-foreground"
        >
          <Menu className="size-5" />
          更多
        </button>
      </nav>

      <Toaster position="top-right" richColors closeButton />
      <ConfirmHost />
    </div>
  );
};
