import { FileText, MessageSquare, Radar, Settings as SettingsIcon } from "lucide-react";
import { NavLink, Route, Routes } from "react-router-dom";
import { LanguageProvider, LanguageSelector, useLanguage } from "./components/LanguageSelector";
import { ProjectProvider, ProjectSelector } from "./components/ProjectSelector";
import ThemeToggle from "./components/ThemeToggle";
import Chat from "./routes/Chat";
import Docs from "./routes/Docs";
import Settings from "./routes/Settings";

function Sidebar() {
  const { t } = useLanguage();
  const navItems = [
    { to: "/", label: t("nav_chat"), icon: MessageSquare, end: true },
    { to: "/docs", label: t("nav_docs"), icon: FileText, end: false },
    { to: "/settings", label: t("nav_settings"), icon: SettingsIcon, end: false },
  ];

  return (
    <aside className="flex w-60 shrink-0 flex-col gap-6 border-r border-beacon-11 p-4 dark:border-beacon-6">
      <div className="flex items-center gap-2 px-2 pt-1">
        <Radar size={22} strokeWidth={1.75} className="text-beacon-9 dark:text-beacon-10" />
        <span className="font-display text-xl font-bold tracking-wide text-beacon-9 dark:text-beacon-10">
          Beacon
        </span>
      </div>
      <nav className="flex flex-col gap-1">
        {navItems.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "bg-beacon-11 font-medium text-beacon-9 dark:bg-beacon-9/15 dark:text-beacon-10"
                  : "text-stone-600 hover:bg-beacon-11 dark:text-stone-400 dark:hover:bg-beacon-4"
              }`
            }
          >
            <Icon size={17} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-2 px-2">
        <ThemeToggle />
        <LanguageSelector />
        <ProjectSelector />
      </div>
    </aside>
  );
}

export default function App() {
  return (
    <LanguageProvider>
      <ProjectProvider>
        <div className="flex h-screen bg-beacon-12 text-beacon-2 dark:bg-beacon-2 dark:text-beacon-11">
          <Sidebar />
          <main className="flex-1 overflow-y-auto px-10 py-8">
            <div className="mx-auto max-w-5xl">
              <Routes>
                <Route path="/" element={<Chat />} />
                <Route path="/docs" element={<Docs />} />
                <Route path="/settings" element={<Settings />} />
              </Routes>
            </div>
          </main>
        </div>
      </ProjectProvider>
    </LanguageProvider>
  );
}
