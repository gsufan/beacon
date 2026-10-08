import { FileText, MessageSquare, Settings as SettingsIcon } from "lucide-react";
import { NavLink, Route, Routes, useLocation } from "react-router";
import { LanguageProvider, LanguageSelector, useLanguage } from "./components/LanguageSelector";
import { ProjectProvider, ProjectSelector, useChunkCount } from "./components/ProjectSelector";
import ThemeToggle from "./components/ThemeToggle";
import Chat from "./routes/Chat";
import Docs from "./routes/Docs";
import Settings from "./routes/Settings";

function BeaconMark() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="6" cy="12" r="2.5" fill="currentColor" stroke="none" />
      <path d="M11 7a7 7 0 0 1 0 10" />
      <path d="M15 4a11 11 0 0 1 0 16" />
    </svg>
  );
}

function Sidebar() {
  const { language, t } = useLanguage();
  // Refetch on navigation so the count is fresh after a sync in Settings.
  const chunkCount = useChunkCount(useLocation().pathname);
  const navItems = [
    { to: "/", label: t("nav_chat"), icon: MessageSquare, end: true },
    { to: "/docs", label: t("nav_docs"), icon: FileText, end: false },
    { to: "/settings", label: t("nav_settings"), icon: SettingsIcon, end: false },
  ];
  // Manual grouping: the "es" locale leaves four-digit numbers ungrouped.
  const formattedCount =
    chunkCount === null ? null : String(chunkCount).replace(/\B(?=(\d{3})+(?!\d))/g, language === "es" ? "." : ",");

  return (
    <aside className="flex w-[232px] shrink-0 flex-col gap-6 border-r border-line bg-surface px-4 py-5">
      <div className="flex items-center gap-2.5 px-2 text-accent-ink">
        <BeaconMark />
        <span className="font-display text-xl font-bold tracking-wide">Beacon</span>
      </div>

      <div className="flex flex-col gap-1.5 rounded-xl border border-line bg-ground p-3">
        <label htmlFor="project-select" className="text-xs font-semibold text-muted">
          {t("sidebar_project")}
        </label>
        <ProjectSelector id="project-select" />
        {formattedCount !== null && (
          <div className="flex items-center gap-1.5 text-xs text-muted">
            <span className="h-2 w-2 rounded-full bg-ok" />
            <span>{t("sidebar_chunks", { n: formattedCount })}</span>
          </div>
        )}
      </div>

      <nav className="flex flex-col gap-0.5">
        {navItems.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex h-[38px] items-center gap-2.5 rounded-lg px-3 text-sm transition-colors ${
                isActive ? "bg-accent-tint font-semibold text-accent-ink" : "text-ink-2 hover:bg-surface-2"
              }`
            }
          >
            <Icon size={17} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto flex flex-col gap-2.5">
        <ThemeToggle />
        <div className="flex items-center justify-between gap-2">
          <label htmlFor="language-select" className="text-xs text-muted">
            {t("sidebar_language")}
          </label>
          <LanguageSelector id="language-select" />
        </div>
      </div>
    </aside>
  );
}

export default function App() {
  return (
    <LanguageProvider>
      <ProjectProvider>
        <div className="flex h-screen bg-ground text-ink">
          <Sidebar />
          <main className="min-w-0 flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<Chat />} />
              <Route path="/docs" element={<Docs />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </main>
        </div>
      </ProjectProvider>
    </LanguageProvider>
  );
}
