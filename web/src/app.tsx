import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import LoginGate from '@/components/LoginGate';
import DashboardPage from '@/pages/DashboardPage/DashboardPage';
import TasksPage from '@/pages/TasksPage/TasksPage';
import ConfigPage from '@/pages/ConfigPage/ConfigPage';
import OrganizePage from '@/pages/OrganizePage/OrganizePage';
import LogsPage from '@/pages/LogsPage/LogsPage';
import StoragePage from '@/pages/StoragePage/StoragePage';
import SourcesPage from '@/pages/SourcesPage/SourcesPage';
import StatsPage from '@/pages/StatsPage/StatsPage';
import LibraryPage from '@/pages/LibraryPage/LibraryPage';
import NotFoundPage from '@/pages/NotFoundPage/NotFoundPage';
import { extras } from '@/api/extras';

export default function App() {
  const [needAuth, setNeedAuth] = useState<boolean | null>(null);

  useEffect(() => {
    extras.authStatus()
      .then((s) => setNeedAuth(s.auth_required))
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
        <Route path="tasks" element={<TasksPage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="extensions" element={<ConfigPage key="extensions" initialTab="extensions" />} />
        <Route path="organize" element={<OrganizePage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="storage" element={<StoragePage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="stats" element={<StatsPage />} />
        <Route path="library" element={<LibraryPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
