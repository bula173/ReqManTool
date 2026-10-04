import { Route, Routes } from 'react-router-dom';
import { AppLayout } from './layout/AppLayout';
import { HomePage } from './pages/HomePage';
import { ModulePage } from './pages/ModulePage';
import { ProjectPage } from './pages/ProjectPage';

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/projects/:projectId" element={<ProjectPage />} />
        <Route path="/projects/:projectId/modules/:moduleId" element={<ModulePage />} />
      </Route>
    </Routes>
  );
}
