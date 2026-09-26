import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import DashboardPage from '@/pages/DashboardPage/DashboardPage';
import TasksPage from '@/pages/TasksPage/TasksPage';
import ConfigPage from '@/pages/ConfigPage/ConfigPage';
import OrganizePage from '@/pages/OrganizePage/OrganizePage';
import NotFoundPage from '@/pages/NotFoundPage/NotFoundPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="organize" element={<OrganizePage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
