import type { ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { MetaProvider, useMeta } from "./components/MetaContext";
import { SchedulerPill } from "./components/SchedulerPill";
import { ToastProvider } from "./components/Toasts";
import { BacktestPage } from "./pages/BacktestPage";
import { HistoryPage } from "./pages/HistoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { CurrentWeekRedirect, WeekPage } from "./pages/WeekPage";

function NavBar() {
  const { meta } = useMeta();
  const loc = useLocation();
  const weekHref = meta ? `/week/${meta.current.season}/${meta.current.week}` : "/";
  return (
    <nav className="nav" aria-label="Main">
      <NavLink to="/" className="brand" end aria-label="Cover 5 home">
        <span className="brand-mark" aria-hidden="true">
          5
        </span>
        Cover 5
      </NavLink>
      <div className="nav-links">
        <NavLink to={weekHref} className={({ isActive }) => (isActive || loc.pathname.startsWith("/week") ? "active" : "")}>
          Week
        </NavLink>
        <NavLink to="/history">History</NavLink>
        <NavLink to="/backtest">Backtest</NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </div>
      <div className="nav-right">
        <SchedulerPill status={meta?.scheduler} />
      </div>
    </nav>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<CurrentWeekRedirect />} />
      <Route path="/week" element={<CurrentWeekRedirect />} />
      <Route path="/week/:season/:week" element={<WeekPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/backtest" element={<BacktestPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function Providers({ children, pollMs }: { children: ReactNode; pollMs?: number }) {
  return (
    <ToastProvider>
      <MetaProvider pollMs={pollMs}>{children}</MetaProvider>
    </ToastProvider>
  );
}

export function App() {
  return (
    <Providers>
      <NavBar />
      <main className="main">
        <AppRoutes />
      </main>
    </Providers>
  );
}

export { NavBar };
