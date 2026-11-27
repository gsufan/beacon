import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { hub } from "../lib/hub";
import { getSession, setSession, type SessionUser, type StoredSession } from "../lib/session";

type Mode = "loading" | "team" | "standalone";

interface AuthContextValue {
  mode: Mode;
  user: SessionUser | null;
  licenseExpired: boolean;
  signIn: (s: StoredSession) => void;
  signOut: () => void;
  refresh: () => Promise<void>;
  // Team mode: what the person may do. In standalone mode everything is allowed (the instance is not shared).
  isAdmin: boolean;
  roleIn: (projectId: string | null) => string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const REFRESH_MS = 20 * 60 * 1000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<Mode>("loading");
  const [user, setUser] = useState<SessionUser | null>(() => getSession()?.user ?? null);
  const [licenseExpired, setLicenseExpired] = useState(false);

  const signIn = useCallback((s: StoredSession) => {
    setSession(s);
    setUser(s.user);
    setLicenseExpired(false);
  }, []);

  const signOut = useCallback(() => {
    setSession(null);
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    if (!getSession()) return;
    try {
      const s = await hub.refresh();
      setSession(s); // keeps the license warning: a refresh does not renew an expired license
      setUser(s.user);
    } catch {
      /* the request itself handles an expired session */
    }
  }, []);

  useEffect(() => {
    hub
      .status()
      .then((s) => setMode(s.mode))
      .catch(() => setMode("standalone"));
  }, []);

  useEffect(() => {
    if (mode !== "team") return;
    refresh(); // roles and plan may have changed since the last login
    const id = window.setInterval(refresh, REFRESH_MS);
    const expired = () => signOut();
    const noLicense = () => setLicenseExpired(true);
    window.addEventListener("beacon:session-expired", expired);
    window.addEventListener("beacon:license-expired", noLicense);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("beacon:session-expired", expired);
      window.removeEventListener("beacon:license-expired", noLicense);
    };
  }, [mode, refresh, signOut]);

  const team = mode === "team";
  const isAdmin = !team || Boolean(user?.admin);
  const roleIn = (projectId: string | null) => {
    if (!team) return "admin";
    if (user?.admin) return "admin";
    return projectId ? (user?.roles[projectId] ?? null) : null;
  };

  return (
    <AuthContext.Provider value={{ mode, user, licenseExpired, signIn, signOut, refresh, isAdmin, roleIn }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de AuthProvider");
  return ctx;
}

export function canSync(role: string | null) {
  return role === "admin" || role === "product_owner" || role === "scrum_master";
}
