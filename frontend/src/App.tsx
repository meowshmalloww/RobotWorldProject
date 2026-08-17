import { Suspense, lazy, useState } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/shell/Sidebar";
import { StatusBar, Topbar } from "./components/shell/Topbar";

const Overview = lazy(() => import("./pages/Overview"));
const Skills = lazy(() => import("./pages/Skills"));
const SkillDetail = lazy(() => import("./pages/SkillDetail"));
const Assets = lazy(() => import("./pages/Assets"));
const AssetDetail = lazy(() => import("./pages/AssetDetail"));
const Worlds = lazy(() => import("./pages/Worlds"));
const LiveEvaluation = lazy(() => import("./pages/LiveEvaluation"));
const Sources = lazy(() => import("./pages/Sources"));
const Training = lazy(() => import("./pages/Training"));
const Observability = lazy(() => import("./pages/Observability"));
const Services = lazy(() => import("./pages/Services"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <HashRouter>
      <div className={`app-shell ${collapsed ? "collapsed" : ""}`}>
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
        <Topbar />
        <main className="main">
          <Suspense fallback={<div className="page" style={{ color: "var(--text-3)", fontSize: "var(--fs-body)" }}>Loading…</div>}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/skills" element={<Skills />} />
              <Route path="/skills/:skillId" element={<SkillDetail />} />
              <Route path="/assets" element={<Assets />} />
              <Route path="/assets/:assetId" element={<AssetDetail />} />
              <Route path="/worlds" element={<Worlds />} />
              <Route path="/worlds/live" element={<LiveEvaluation />} />
              <Route path="/sources" element={<Sources />} />
              <Route path="/training" element={<Training />} />
              <Route path="/observability" element={<Observability />} />
              <Route path="/services" element={<Services />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
        <StatusBar />
      </div>
    </HashRouter>
  );
}
