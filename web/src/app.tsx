import { lazy, useEffect, useState } from 'react';
import { ErrorBoundary } from 'react-error-boundary';
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from 'react-router-dom';
import { authApi } from '@/api/auth';
import { ApiError, onUnauthorized } from '@/api/request';
import { ErrorFallback } from '@/components/ErrorFallback';
import { Layout } from '@/components/Layout';
import LoginGate, { AccessDenied, SetupGate } from '@/components/LoginGate';
import DashboardPage from '@/pages/DashboardPage/DashboardPage';
import InboxPage from '@/pages/InboxPage/InboxPage';
import TasksPage from '@/pages/TasksPage/TasksPage';
import LibraryPage from '@/pages/LibraryPage/LibraryPage';
import NotFoundPage from '@/pages/NotFoundPage/NotFoundPage';

// 常用页面直接打包；统计页（echarts）、设置、插件与工具页按需加载，控制首屏体积。
const StatsPage = lazy(() => import('@/pages/StatsPage/StatsPage'));
const SettingsPage = lazy(() => import('@/pages/ConfigPage/SettingsPage'));
const PluginsPage = lazy(() => import('@/pages/ConfigPage/PluginsPage'));
const StoragePage = lazy(() => import('@/pages/StoragePage/StoragePage'));
const LogsPage = lazy(() => import('@/pages/LogsPage/LogsPage'));
const SubscriptionsPage = lazy(() => import('@/pages/SubscriptionsPage/SubscriptionsPage'));

/** 根路由：还没有设置登录密码时先显示设置密码页（首次部署）；开启了密码且未登录时，任何地址都显示登录页；
 *  登录后回到原来的页面。服务端拒绝访问时整页说明原因。页面渲染出错时显示 ErrorFallback（换了地址自动重试）。 */
function Root() {
  const [needAuth, setNeedAuth] = useState<boolean | null>(null);
  const [setup, setSetup] = useState<{ blocked: string | null } | null>(null);
  const [denied, setDenied] = useState('');
  const { pathname } = useLocation();

  useEffect(() => {
    authApi.status()
      .then((s) => {
        if (s.setup_required) setSetup({ blocked: s.setup_blocked ?? null });
        setNeedAuth(s.auth_required && !s.authenticated);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) setDenied(e.message);
        setNeedAuth(false);
      });
  }, []);

  // 会话失效（如改了密码、会话过期）：回到登录页，控制台各页面卸载后不再轮询
  useEffect(() => onUnauthorized(() => setNeedAuth(true)), []);

  if (denied) return <AccessDenied message={denied} />;
  if (setup) {
    return <SetupGate blocked={setup.blocked} onDone={() => { setSetup(null); setNeedAuth(false); }}
      onExisting={() => { setSetup(null); setNeedAuth(true); }} />;
  }
  if (needAuth === null) return null;
  return (
    <ErrorBoundary FallbackComponent={ErrorFallback} resetKeys={[pathname]}>
      {needAuth ? <LoginGate onOk={() => setNeedAuth(false)} /> : <Outlet />}
    </ErrorBoundary>
  );
}

// 数据路由：设置页离开前确认未保存的修改要用到 useBlocker。
const router = createBrowserRouter([{
  element: <Root />,
  children: [
    {
      path: '/',
      element: <Layout />,
      children: [
        { index: true, element: <Navigate to="/dashboard" replace /> },
        { path: 'dashboard', element: <DashboardPage /> },
        { path: 'inbox', element: <InboxPage /> },
        { path: 'tasks', element: <TasksPage /> },
        { path: 'config', element: <SettingsPage /> },
        { path: 'extensions', element: <PluginsPage /> },
        { path: 'logs', element: <LogsPage /> },
        { path: 'storage', element: <StoragePage /> },
        { path: 'stats', element: <StatsPage /> },
        { path: 'library', element: <LibraryPage /> },
        { path: 'subscriptions', element: <SubscriptionsPage /> },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ],
}], { basename: import.meta.env.BASE_URL });

export default function App() {
  return <RouterProvider router={router} />;
}
