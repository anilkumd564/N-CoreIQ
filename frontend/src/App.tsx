import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "./components/Layout/AppShell";
import { OverviewPage } from "./pages/OverviewPage";
import { RepositoryExplorerPage } from "./pages/RepositoryExplorerPage";
import { DependenciesPage } from "./pages/DependenciesPage";
import { JobsPage } from "./pages/JobsPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { ExportsPage } from "./pages/ExportsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage />} />
        <Route path="repository" element={<RepositoryExplorerPage />} />
        <Route path="repository/:objectType/:objectName" element={<RepositoryExplorerPage />} />
        <Route path="repository/:objectType/:objectName/:tab" element={<RepositoryExplorerPage />} />
        <Route path="dependencies" element={<DependenciesPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
        <Route path="exports" element={<ExportsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
