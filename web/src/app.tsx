import { lazy, useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import LoginGate from '@/components/LoginGate';
import DashboardPage from '@/pages/DashboardPage/DashboardPage';
import InboxPage from '@/pages/InboxPage/InboxPage';
import TasksPage from '@/pages/TasksPage/TasksPage';
import LibraryPage from '@/pages/LibraryPage/LibraryPage';
import NotFoundPage from '@/pages/NotFoundPage/NotFoundPage';
import { extras } from '@/api/extras';

// 常用页面直接打包；统计页（echarts）、设置与工具页按需加载，控制首屏体积。
const StatsPage = lazy(() => import('@/pages/StatsPage/StatsPage'));
const ConfigPage = lazy(() => import('@/pages/ConfigPage/ConfigPage'));
const SourcesPage = lazy(() => import('@/pages/SourcesPage/SourcesPage'));
const OrganizePage = lazy(() => import('@/pages/OrganizePage/OrganizePage'));
const StoragePage = lazy(() => import('@/pages/StoragePage/StoragePage'));
const LogsPage = lazy(() => import('@/pages/LogsPage/LogsPage'));
const SubscriptionsPage = lazy(() => import('@/pages/SubscriptionsPage/SubscriptionsPage'));

export default function App() {
  const [needAuth, setNeedAuth] = useState<boolean | null>(null);

  useEffect(() => {
    extras.authStatus()
      .then((s) => setNeedAuth(s.auth_required && !s.authenticated))
      .catch(() => setNeedAuth(false));
  }, []);

  if (needAuth === null) return null;
  if (needAuth) {
    return (
      <Routes>
        <Route path="*" element={<LoginGate onOk={() => setNeedAuth(false)} />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="extensions" element={<ConfigPage key="extensions" initialTab="extensions" />} />
        <Route path="organize" element={<OrganizePage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="storage" element={<StoragePage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="stats" element={<StatsPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="subscriptions" element={<SubscriptionsPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
