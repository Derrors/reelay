import { Film, FolderCog, FolderTree, LayoutDashboard, ListChecks, ScrollText, Settings2, TestTube2, TrendingUp, LogOut } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Toaster } from '@/components/ui/sonner';
import { extras } from '@/api/extras';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { path: '/dashboard', label: '运行总览', icon: LayoutDashboard },
  { path: '/tasks', label: '任务中心', icon: ListChecks },
  { path: '/config', label: '规则配置', icon: Settings2 },
  { path: '/organize', label: '整理预览', icon: FolderCog },
  { path: '/sources', label: '源与解析测试', icon: TestTube2 },
  { path: '/storage', label: '115 目录', icon: FolderTree },
  { path: '/stats', label: '统计', icon: TrendingUp },
  { path: '/logs', label: '运行日志', icon: ScrollText },
];

export const Layout = () => {
  return (
    <div className="min-h-screen bg-background text-foreground lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="border-sidebar-border bg-sidebar lg:sticky lg:top-0 lg:h-screen lg:border-r">
        <div className="flex h-16 items-center gap-3 border-b border-sidebar-border px-5">
          <div className="flex size-9 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
            <Film className="size-5" />
          </div>
          <div>
            <div className="font-semibold tracking-tight">Reelay</div>
            <div className="text-xs text-muted-foreground">影视转存编目台</div>
          </div>
        </div>
        <nav className="flex gap-2 overflow-x-auto p-3 lg:flex-col">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) => cn(
                  'flex min-w-fit items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                    : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground',
                )}
              >
                <Icon className="size-4" />
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="hidden px-5 pt-4 text-xs leading-5 text-muted-foreground lg:block">
          Telegram 消息进入任务队列后，由 AI 识别，再交给 115 执行与验证。
        </div>
        <div className="mt-auto hidden px-3 pb-4 lg:block">
          <LogoutButton />
        </div>
      </aside>
      <main className="min-w-0 overflow-hidden">
        <Outlet />
      </main>
      <Toaster position="top-right" richColors />
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
      className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium text-sidebar-foreground/70 hover:bg-sidebar-accent/60"
    >
      <LogOut className="size-4" />退出登录
    </button>
  );
}
