// Session and license signed by Beacon Hub (team mode). Kept in localStorage and sent on every request.
export interface SessionUser {
  sub: number;
  org: number;
  name: string;
  email: string;
  admin: boolean;
  github: string | null;
  roles: Record<string, string>;
  exp: number;
}

export interface StoredSession {
  session: string;
  license: string;
  user: SessionUser;
}

const KEY = "beacon:session";

export function getSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as StoredSession;
    return s.user.exp * 1000 > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function setSession(s: StoredSession | null) {
  if (s) localStorage.setItem(KEY, JSON.stringify({ session: s.session, license: s.license, user: s.user }));
  else localStorage.removeItem(KEY);
}

export function sessionHeaders(): Record<string, string> {
  const s = getSession();
  return s ? { Authorization: `Bearer ${s.session}`, "X-Beacon-License": s.license } : {};
}
