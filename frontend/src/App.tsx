import { Briefcase, FileText, LogOut, MessageSquare, Settings as SettingsIcon, Users } from "lucide-react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router";
import { AuthProvider, useAuth } from "./components/AuthProvider";
import { LanguageProvider, LanguageSelector, useLanguage } from "./components/LanguageSelector";
import { ProjectProvider, ProjectSelector, useChunkCount, useProject } from "./components/ProjectSelector";
import ThemeToggle from "./components/ThemeToggle";
import { Avatar } from "./components/Colorful";
import { InstanceRegisterBanner, InstanceRevoked } from "./components/InstanceGate";
import Chat from "./routes/Chat";
import Docs from "./routes/Docs";
import Login from "./routes/Login";
import Settings from "./routes/Settings";
import Study from "./routes/Study";
import Team from "./routes/Team";
import Work from "./routes/Work";

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
  const { mode, user, isAdmin, roleIn, signOut } = useAuth();
  const { projectId } = useProject();
  const team = mode === "team";
  const managesSomething = isAdmin || Object.values(user?.roles ?? {}).some((r) => r === "product_owner" || r === "scrum_master");
  const navItems = team
    ? [
        { to: "/", label: t("nav_work"), icon: Briefcase, end: true },
        { to: "/chat", label: t("nav_chat"), icon: MessageSquare, end: false },
        { to: "/docs", label: t("nav_docs"), icon: FileText, end: false },
        ...(managesSomething ? [{ to: "/team", label: t("nav_team"), icon: Users, end: false }] : []),
        ...(isAdmin ? [{ to: "/settings", label: t("nav_settings"), icon: SettingsIcon, end: false }] : []),
      ]
    : [
        { to: "/", label: t("nav_chat"), icon: MessageSquare, end: true },
        { to: "/docs", label: t("nav_docs"), icon: FileText, end: false },
        { to: "/settings", label: t("nav_settings"), icon: SettingsIcon, end: false },
      ];
  const role = roleIn(projectId);
  // Manual grouping: the "es" locale leaves four-digit numbers ungrouped.
  const formattedCount =
    chunkCount === null ? null : String(chunkCount).replace(/\B(?=(\d{3})+(?!\d))/g, language === "es" ? "." : ",");

  return (
    <aside className="flex w-[240px] shrink-0 flex-col gap-6 bg-gradient-to-b from-side-bg to-side-bg2 px-4 py-5 text-side-ink">
      <div className="flex items-center gap-2.5 px-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-[var(--c5)] to-[var(--c2)] text-white shadow-sm">
          <BeaconMark />
        </span>
        <span className="font-display text-xl font-bold tracking-wide">Beacon</span>
      </div>

      <div className="flex flex-col gap-1.5 rounded-xl border border-side-line bg-side-card p-3">
        <label htmlFor="project-select" className="text-xs font-semibold text-side-muted">
          {t("sidebar_project")}
        </label>
        <ProjectSelector id="project-select" />
        {formattedCount !== null && (
          <div className="flex items-center gap-1.5 text-xs text-side-muted">
            <span className="h-2 w-2 rounded-full bg-[#4ade80]" />
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
                isActive ? "bg-side-active font-semibold text-side-ink" : "text-side-muted hover:bg-side-hover hover:text-side-ink"
              }`
            }
          >
            <Icon size={17} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto flex flex-col gap-2.5">
        {team && user && (
          <div className="flex items-center gap-2.5 rounded-xl border border-side-line bg-side-card px-3 py-2.5">
            <Avatar name={user.name} size={30} />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-sm font-semibold">{user.name}</span>
              <span className="truncate text-xs text-side-muted">{role ? t(`role_${role}` as "role_admin") : t("role_none")}</span>
            </div>
            <button type="button" onClick={signOut} className="shrink-0 text-side-muted hover:text-side-ink" aria-label={t("logout")} title={t("logout")}>
              <LogOut size={16} />
            </button>
          </div>
        )}
        <ThemeToggle />
        <div className="flex items-center justify-between gap-2">
          <label htmlFor="language-select" className="text-xs text-side-muted">
            {t("sidebar_language")}
          </label>
          <LanguageSelector id="language-select" />
        </div>
      </div>
    </aside>
  );
}

function LicenseBanner() {
  const { t } = useLanguage();
  const { licenseExpired, isAdmin } = useAuth();
  if (!licenseExpired) return null;
  return (
    <div role="alert" className="border-b border-danger-line bg-danger-bg px-6 py-2.5 text-sm text-danger-ink">
      {t("license_expired")} {isAdmin && <NavLink to="/team" className="font-semibold underline">{t("license_renew")}</NavLink>}
    </div>
  );
}

function Shell() {
  const { mode, user, isAdmin, instance } = useAuth();
  if (mode === "loading") return <div className="h-screen bg-ground" />;
  if (instance?.revoked) return <InstanceRevoked />;
  if (mode === "team" && !user) return <Login />;
  const team = mode === "team";
  return (
    <ProjectProvider key={user?.sub ?? "standalone"}>
      <div className="flex h-screen bg-ground text-ink">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <LicenseBanner />
          {team && <InstanceRegisterBanner />}
          <Routes>
            <Route path="/" element={team ? <Work /> : <Chat />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/docs" element={<Docs />} />
            <Route path="/team" element={team ? <Team /> : <Navigate to="/" />} />
            <Route path="/settings" element={isAdmin ? <Settings /> : <Navigate to="/" />} />
          </Routes>
        </main>
      </div>
    </ProjectProvider>
  );
}

export default function App() {
  // Study mode (`beacon study`) is a standalone screen, without sidebar or projects.
  if (useLocation().pathname.startsWith("/study")) return <Study />;
  return (
    <LanguageProvider>
      <AuthProvider>
        <Shell />
      </AuthProvider>
    </LanguageProvider>
  );
}
