import {
  Clapperboard, FolderCog, FolderTree, LayoutDashboard, LibraryBig, ListChecks,
  LogOut, Puzzle, RadioTower, ScrollText, Settings2, TrendingUp,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Toaster } from '@/components/ui/sonner';
import { extras } from '@/api/extras';
import { cn } from '@/lib/utils';

interface NavEntry {
  path: string;
  label: string;
  icon: LucideIcon;
}
interface NavGroup {
  title: string;
  items: NavEntry[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: '监控',
    items: [
      { path: '/dashboard', label: '运行总览', icon: LayoutDashboard },
      { path: '/tasks', label: '待办与任务', icon: ListChecks },
    ],
  },
  {
    title: '资源',
    items: [
      { path: '/library', label: '媒体库', icon: LibraryBig },
      { path: '/stats', label: '转存统计', icon: TrendingUp },
    ],
  },
  {
    title: '设置',
    items: [
      { path: '/sources', label: '监听源测试', icon: RadioTower },
      { path: '/organize', label: '整理命名预览', icon: FolderCog },
      { path: '/storage', label: '115 目录', icon: FolderTree },
      { path: '/config', label: '系统设置', icon: Settings2 },
      { path: '/extensions', label: '插件与扩展', icon: Puzzle },
      { path: '/logs', label: '运行日志', icon: ScrollText },
    ],
  },
];

export const Layout = () => {
  return (
    <div className="min-h-screen bg-transparent text-foreground lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="border-sidebar-border/70 bg-sidebar text-sidebar-foreground lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col lg:border-r">
        {/* 品牌区 */}
        <div className="relative flex h-16 items-center gap-3 overflow-hidden border-b border-sidebar-border/70 px-5">
          <div
            className="pointer-events-none absolute inset-0 opacity-60"
            style={{ background: 'radial-gradient(420px 120px at 0% -40%, rgba(64,200,180,.22), transparent 70%)' }}
          />
          <div className="relative flex size-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-[#2bb5a3] to-[#137a6e] text-white shadow-[0_4px_14px_-2px_rgba(20,160,145,.55)]">
            <Clapperboard className="size-[18px]" />
          </div>
          <div className="relative leading-tight">
            <div className="text-[15px] font-semibold tracking-tight text-white">Reelay</div>
            <div className="text-[11px] text-sidebar-foreground/55">Telegram → 115 智能转存</div>
          </div>
        </div>

        {/* 导航 */}
        <nav className="flex gap-1.5 overflow-x-auto px-3 pb-3 pt-3 lg:flex-1 lg:flex-col lg:overflow-y-auto lg:pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {NAV_GROUPS.map((group, gi) => (
            <div key={group.title} className="contents lg:block">
              {gi > 0 && <div className="mx-2 my-3 hidden h-px bg-sidebar-border/60 lg:block" />}
              <div className="hidden px-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-sidebar-foreground/40 lg:block">
                {group.title}
              </div>
              <div className="contents lg:block lg:space-y-0.5">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      className={({ isActive }) =>
                        cn(
                          'relative flex min-w-fit items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-all duration-150',
                          isActive
                            ? 'bg-sidebar-accent text-sidebar-accent-foreground shadow-[inset_0_1px_0_rgba(255,255,255,.04)]'
                            : 'text-sidebar-foreground/62 hover:bg-sidebar-accent/55 hover:text-sidebar-foreground',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          <span
                            className={cn(
                              'absolute left-0 top-1/2 hidden h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-sidebar-primary transition-all lg:block',
                              isActive ? 'opacity-100' : 'opacity-0',
                            )}
                          />
                          <Icon className={cn('size-4 shrink-0', isActive ? 'text-sidebar-primary' : 'text-sidebar-foreground/50')} />
                          {item.label}
                        </>
                      )}
                    </NavLink>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* 底部：退出 */}
        <div className="hidden border-t border-sidebar-border/70 p-3 lg:block">
          <LogoutButton />
        </div>
      </aside>

      <main className="min-w-0">
        <Outlet />
      </main>
      <Toaster position="top-right" richColors closeButton />
    </div>
  );
};

function LogoutButton() {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    extras.authStatus().then((r) => setEnabled(r.auth_required)).catch(() => undefined);
  }, []);
  if (!enabled) return null;
  return (
    <button
      onClick={() => { void extras.logout().then(() => window.location.reload()); }}
      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium text-sidebar-foreground/62 transition-colors hover:bg-sidebar-accent/55 hover:text-sidebar-foreground"
    >
      <LogOut className="size-4 text-sidebar-foreground/50" />退出登录
    </button>
  );
}
