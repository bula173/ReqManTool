import { Route, Routes } from 'react-router-dom';
import { AppLayout } from './layout/AppLayout';
import { DetachedLayout } from './layout/DetachedLayout';
import { BaselineComparePage } from './pages/BaselineComparePage';
import { BaselinePage } from './pages/BaselinePage';
import { CommitBrowserPage } from './pages/CommitBrowserPage';
import { CommitDetailsPage } from './pages/CommitDetailsPage';
import { ExecutionReportPage } from './pages/ExecutionReportPage';
import { HomePage } from './pages/HomePage';
import { ImpactAnalysisPage } from './pages/ImpactAnalysisPage';
import { ModulePage } from './pages/ModulePage';
import { ProjectPage } from './pages/ProjectPage';
import { TraceabilityPage } from './pages/TraceabilityPage';
import { VerificationReportPage } from './pages/VerificationReportPage';

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
        {/* Commit browser (read-only): the list (?module=&requirement=), a commit, the project at a commit. */}
        <Route path="/projects/:projectId/commits" element={<CommitBrowserPage />} />
        <Route path="/projects/:projectId/commits/:commit" element={<CommitDetailsPage />} />
        <Route path="/projects/:projectId/commits/:commit/browse" element={<BaselinePage />} />
        {/* Reports: the module is `:reportModuleId`, not `:moduleId`, so a report does not open the module (no tab). */}
        <Route path="/projects/:projectId/reports/traceability" element={<TraceabilityPage />} />
        {/* Impact analysis takes its starting requirements from the URL (`?req=`, repeatable). */}
        <Route path="/projects/:projectId/reports/impact" element={<ImpactAnalysisPage />} />
        <Route path="/projects/:projectId/reports/verification/:reportModuleId" element={<VerificationReportPage />} />
        <Route path="/projects/:projectId/reports/execution/:reportModuleId" element={<ExecutionReportPage />} />
      </Route>
      {/* A module in its own browser window (minimal chrome); see layout/moduleTabs.ts. */}
      <Route path="/detached" element={<DetachedLayout />}>
        <Route path="projects/:projectId/modules/:moduleId" element={<ModulePage />} />
      </Route>
    </Routes>
  );
}
