import { FileText, MessageSquare, Radar, Settings as SettingsIcon } from "lucide-react";
import { NavLink, Route, Routes } from "react-router-dom";
import { LanguageProvider, LanguageSelector, useLanguage } from "./components/LanguageSelector";
import { ProjectProvider, ProjectSelector } from "./components/ProjectSelector";
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
    <aside className="flex w-60 shrink-0 flex-col gap-6 border-r border-stone-200 p-4 dark:border-stone-700">
      <div className="flex items-center gap-2 px-2 pt-1">
        <Radar size={20} strokeWidth={1.75} className="text-amber-600 dark:text-amber-400" />
        <span className="text-base font-semibold tracking-tight">Beacon</span>
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
                  ? "bg-amber-50 font-medium text-amber-700 dark:bg-amber-400/10 dark:text-amber-400"
                  : "text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800"
              }`
            }
          >
            <Icon size={17} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-2 px-2">
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
        <div className="flex h-screen bg-stone-50 text-stone-900 dark:bg-stone-900 dark:text-stone-100">
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
