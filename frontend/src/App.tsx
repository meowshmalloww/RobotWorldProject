import { Suspense, lazy, useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/shell/Sidebar";
import { StatusBar, Titlebar } from "./components/shell/Titlebar";
import { ToastProvider } from "./components/ui/Toast";
import { installGlobalDiagnostics } from "./lib/runtimeDiagnostics";

const Overview = lazy(() => import("./pages/Overview"));
const Skills = lazy(() => import("./pages/Skills"));
const SkillDetail = lazy(() => import("./pages/SkillDetail"));
const Assets = lazy(() => import("./pages/Assets"));
const AssetDetail = lazy(() => import("./pages/AssetDetail"));
const Worlds = lazy(() => import("./pages/Worlds"));
const Sources = lazy(() => import("./pages/Sources"));
const Training = lazy(() => import("./pages/Training"));
const Observability = lazy(() => import("./pages/Observability"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => installGlobalDiagnostics(), []);
  return (
    <HashRouter>
      <ToastProvider>
        <div className={`app-shell ${collapsed ? "collapsed" : ""}`}>
            <Titlebar />
            <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
            <main className="main">
              <AppErrorBoundary>
                <Suspense fallback={<div className="page" style={{ color: "var(--text-3)", fontSize: "var(--fs-body)" }}>Loading...</div>}>
                  <Routes>
                  <Route path="/" element={<Overview />} />
                  <Route path="/skills" element={<Skills />} />
                  <Route path="/skills/:skillId" element={<SkillDetail />} />
                  <Route path="/assets" element={<Assets />} />
                  <Route path="/assets/:assetId" element={<AssetDetail />} />
                  <Route path="/worlds" element={<Worlds />} />
                  <Route path="/sources" element={<Sources />} />
                  <Route path="/training" element={<Training />} />
                  <Route path="/observability" element={<Observability />} />
                  <Route path="/observability/:tab" element={<Observability />} />
                  <Route path="/services" element={<Navigate to="/observability/services" replace />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Suspense>
              </AppErrorBoundary>
            </main>
            <StatusBar />
          </div>
      </ToastProvider>
    </HashRouter>
  );
}
