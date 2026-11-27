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

const REFRESH_GRACE_MS = 12 * 3600 * 1000;

function stored(): StoredSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}

export function getSession(): StoredSession | null {
  const s = stored();
  return s && s.user.exp * 1000 > Date.now() ? s : null;
}

// A session that expired recently can still be renewed with the Hub (if the person is still active).
export function getRenewableSession(): StoredSession | null {
  const s = stored();
  return s && s.user.exp * 1000 + REFRESH_GRACE_MS > Date.now() ? s : null;
}

export function setSession(s: StoredSession | null) {
  if (s) localStorage.setItem(KEY, JSON.stringify({ session: s.session, license: s.license, user: s.user }));
  else localStorage.removeItem(KEY);
}

export function sessionHeaders(): Record<string, string> {
  const s = getRenewableSession();
  return s ? { Authorization: `Bearer ${s.session}`, "X-Beacon-License": s.license } : {};
}
