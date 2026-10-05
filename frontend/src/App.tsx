import { Route, Routes } from 'react-router-dom';
import { AppLayout } from './layout/AppLayout';
import { BaselineComparePage } from './pages/BaselineComparePage';
import { BaselinePage } from './pages/BaselinePage';
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
        {/* The static "compare" segment outranks :baselineName; "compare" is a reserved baseline name. */}
        <Route path="/projects/:projectId/baselines/compare" element={<BaselineComparePage />} />
        <Route path="/projects/:projectId/baselines/:baselineName" element={<BaselinePage />} />
      </Route>
    </Routes>
  );
}
