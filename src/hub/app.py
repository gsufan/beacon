"""Beacon Hub: the cloud side of Beacon. Organizations, people and Scrum roles, licenses with a free trial, the
audit trail and the GitHub connection. It stores metadata only; source code stays in Beacon Local.
"""

import os
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from hub import roles as R
from hub.db import Database
from hub.github import GitHubClient, GitHubError, normalize_repo
from hub.security import (
    InvalidTokenError, hash_password, load_or_create_key, new_code, public_key_text, sign_token, verify_password, verify_token,
)

SESSION_TTL = 8 * 3600
TRIAL_DAYS = 30
PLANS = {
    "trial": {"label": "Prueba", "seats": 5, "price_per_user": 0},
    "equipo": {"label": "Equipo", "seats": 15, "price_per_user": 14990},
    "empresa": {"label": "Empresa", "seats": 60, "price_per_user": 11990},
}
_PROJECT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Hub:
    """State of one Hub instance: database, signing key and the GitHub client factory (replaceable in tests)."""

    def __init__(self, data_dir: Path, github_factory=None):
        self.db = Database(data_dir / "hub.sqlite3")
        self.key = load_or_create_key(data_dir / "hub_signing_key.pem")
        self.public_key = public_key_text(self.key)
        self.github_factory = github_factory or (lambda token: GitHubClient(token))

    # --- tokens
    def roles_of(self, user: dict) -> dict:
        rows = self.db.query("SELECT project_id, role FROM memberships WHERE user_id = ?", (user["id"],))
        return {r["project_id"]: r["role"] for r in rows}

    def session_claims(self, user: dict) -> dict:
        return {"typ": "session", "sub": user["id"], "org": user["org_id"], "name": user["name"], "email": user["email"],
                "admin": bool(user["is_admin"]), "github": user["github_login"], "roles": self.roles_of(user),
                "exp": int(time.time()) + SESSION_TTL}

    def license_token(self, org: dict) -> str:
        return sign_token(self.key, {"typ": "license", "org": org["id"], "org_name": org["name"], "plan": org["plan"],
                                     "seats": org["seats"], "iat": int(time.time()), "exp": int(org["period_end"])})

    def session_response(self, user: dict) -> dict:
        org = self.db.one("SELECT * FROM orgs WHERE id = ?", (user["org_id"],))
        claims = self.session_claims(user)
        return {"session": sign_token(self.key, claims), "license": self.license_token(org), "user": claims,
                "public_key": self.public_key}


# --- request bodies
class SignupBody(BaseModel):
    org_name: str = Field(..., min_length=2, max_length=80)
    name: str = Field(..., min_length=2, max_length=80)
    email: str
    password: str = Field(..., min_length=8)


class LoginBody(BaseModel):
    email: str
    password: str


class InviteBody(BaseModel):
    email: str


class AcceptBody(BaseModel):
    code: str
    name: str = Field(..., min_length=2, max_length=80)
    password: str = Field(..., min_length=8)


class UserPatch(BaseModel):
    github_login: Optional[str] = None
    is_admin: Optional[bool] = None
    active: Optional[bool] = None


class ProjectBody(BaseModel):
    id: str
    name: str = Field(..., min_length=1, max_length=80)
    github_repo: Optional[str] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    github_repo: Optional[str] = None


class MemberBody(BaseModel):
    role: str


class AuditBody(BaseModel):
    project_id: Optional[str] = None
    action: str = Field(..., min_length=1, max_length=60)
    detail: str = Field(default="", max_length=500)


class GitHubBody(BaseModel):
    token: str = Field(default="", max_length=200)


class PlanBody(BaseModel):
    plan: str


def create_app(hub: Hub) -> FastAPI:
    app = FastAPI(title="Beacon Hub", docs_url="/api/docs", redoc_url=None)
    db = hub.db

    def current(authorization: str = Header(default="")) -> dict:
        token = authorization.removeprefix("Bearer ").strip()
        try:
            claims = verify_token(hub.public_key, token, "session")
        except InvalidTokenError as e:
            raise HTTPException(401, f"Sesión inválida: {e}")
        user = db.one("SELECT * FROM users WHERE id = ? AND active = 1", (claims["sub"],))
        if not user:
            raise HTTPException(401, "La cuenta no existe o fue desactivada.")
        user["roles"] = hub.roles_of(user)
        user["admin"] = bool(user["is_admin"])
        return user

    def admin(user: dict = Depends(current)) -> dict:
        if not user["admin"]:
            raise HTTPException(403, "Solo un administrador de la organización puede hacer esto.")
        return user

    def project_of(org_id: int, project_id: str) -> dict:
        project = db.one("SELECT * FROM projects WHERE org_id = ? AND id = ?", (org_id, project_id))
        if not project:
            raise HTTPException(404, f"El proyecto '{project_id}' no existe en la organización.")
        return project

    def require(user: dict, action: str, project_id: str) -> None:
        if not R.can(user, action, project_id):
            raise HTTPException(403, "Tu rol en este proyecto no permite esta acción.")

    def org_summary(org: dict) -> dict:
        seats_used = db.one("SELECT COUNT(*) AS n FROM users WHERE org_id = ? AND active = 1", (org["id"],))["n"]
        return {"id": org["id"], "name": org["name"], "plan": org["plan"], "plan_label": PLANS[org["plan"]]["label"],
                "seats": org["seats"], "seats_used": seats_used, "period_end": org["period_end"],
                "days_left": max(0, int((org["period_end"] - time.time()) // 86400)),
                "expired": org["period_end"] < time.time(), "github_connected": bool(org["github_token"])}

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/public-key")
    def public_key():
        return {"public_key": hub.public_key}

    @app.get("/plans")
    def plans():
        return {"plans": [{"id": k, **v} for k, v in PLANS.items() if k != "trial"], "trial_days": TRIAL_DAYS}

    # --- accounts
    @app.post("/signup", status_code=201)
    def signup(body: SignupBody):
        email = body.email.strip().lower()
        if not _EMAIL.match(email):
            raise HTTPException(400, "Correo no válido.")
        if db.one("SELECT id FROM users WHERE email = ?", (email,)):
            raise HTTPException(409, "Ya existe una cuenta con ese correo.")
        now = time.time()
        org_id = db.execute("INSERT INTO orgs (name, plan, seats, period_end, created) VALUES (?,?,?,?,?)",
                            (body.org_name.strip(), "trial", PLANS["trial"]["seats"], now + TRIAL_DAYS * 86400, now))
        user_id = db.execute("INSERT INTO users (org_id, email, name, password_hash, is_admin, created) VALUES (?,?,?,?,1,?)",
                             (org_id, email, body.name.strip(), hash_password(body.password), now))
        user = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        db.audit(org_id, user, "organización creada", detail=f"prueba de {TRIAL_DAYS} días")
        return hub.session_response(user)

    @app.post("/auth/login")
    def login(body: LoginBody):
        user = db.one("SELECT * FROM users WHERE email = ? AND active = 1", (body.email.strip().lower(),))
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Correo o contraseña incorrectos.")
        db.audit(user["org_id"], user, "inicio de sesión")
        return hub.session_response(user)

    @app.post("/auth/refresh")
    def refresh(user: dict = Depends(current)):
        """New session and license with the current roles and plan (they may have changed since the login)."""
        return hub.session_response(user)

    @app.get("/me")
    def me(user: dict = Depends(current)):
        org = db.one("SELECT * FROM orgs WHERE id = ?", (user["org_id"],))
        return {"user": hub.session_claims(user), "org": org_summary(org)}

    @app.post("/invitations/accept", status_code=201)
    def accept_invitation(body: AcceptBody):
        inv = db.one("SELECT * FROM invitations WHERE code = ? AND used = 0", (body.code.strip(),))
        if not inv:
            raise HTTPException(404, "La invitación no existe o ya se usó.")
        org = db.one("SELECT * FROM orgs WHERE id = ?", (inv["org_id"],))
        if org_summary(org)["seats_used"] >= org["seats"]:
            raise HTTPException(402, "La organización alcanzó el número de usuarios de su plan.")
        if db.one("SELECT id FROM users WHERE email = ?", (inv["email"],)):
            raise HTTPException(409, "Ya existe una cuenta con ese correo.")
        user_id = db.execute("INSERT INTO users (org_id, email, name, password_hash, created) VALUES (?,?,?,?,?)",
                             (inv["org_id"], inv["email"], body.name.strip(), hash_password(body.password), time.time()))
        db.execute("UPDATE invitations SET used = 1 WHERE code = ?", (inv["code"],))
        user = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        db.audit(inv["org_id"], user, "invitación aceptada")
        return hub.session_response(user)

    # --- organization (administrator)
    @app.get("/org")
    def get_org(user: dict = Depends(current)):
        return org_summary(db.one("SELECT * FROM orgs WHERE id = ?", (user["org_id"],)))

    @app.post("/org/plan")
    def choose_plan(body: PlanBody, user: dict = Depends(admin)):
        """Contracts a plan for one month. There is no payment gateway: the payment is simulated and recorded."""
        if body.plan not in PLANS or body.plan == "trial":
            raise HTTPException(400, "Plan desconocido.")
        org = db.one("SELECT * FROM orgs WHERE id = ?", (user["org_id"],))
        if org_summary(org)["seats_used"] > PLANS[body.plan]["seats"]:
            raise HTTPException(409, "La organización tiene más usuarios que los que admite ese plan.")
        start = max(time.time(), org["period_end"]) if org["plan"] == body.plan else time.time()
        db.execute("UPDATE orgs SET plan = ?, seats = ?, period_end = ? WHERE id = ?",
                   (body.plan, PLANS[body.plan]["seats"], start + 30 * 86400, org["id"]))
        db.audit(org["id"], user, "plan contratado", detail=f"{PLANS[body.plan]['label']} (pago simulado)")
        return hub.session_response(db.one("SELECT * FROM users WHERE id = ?", (user["id"],)))

    @app.put("/org/github")
    def set_github(body: GitHubBody, user: dict = Depends(admin)):
        db.execute("UPDATE orgs SET github_token = ? WHERE id = ?", (body.token.strip() or None, user["org_id"]))
        db.audit(user["org_id"], user, "token de GitHub actualizado" if body.token.strip() else "token de GitHub quitado")
        return {"github_connected": bool(body.token.strip())}

    @app.get("/users")
    def list_users(user: dict = Depends(current)):
        rows = db.query("SELECT id, email, name, is_admin, github_login, active FROM users WHERE org_id = ? ORDER BY name",
                        (user["org_id"],))
        if not user["admin"]:  # the rest of the team sees names and GitHub logins, not emails
            rows = [{k: r[k] for k in ("id", "name", "github_login", "active")} for r in rows if r["active"]]
        return {"users": rows}

    @app.patch("/users/{user_id}")
    def patch_user(user_id: int, body: UserPatch, user: dict = Depends(current)):
        target = db.one("SELECT * FROM users WHERE id = ? AND org_id = ?", (user_id, user["org_id"]))
        if not target:
            raise HTTPException(404, "Usuario no encontrado.")
        if not user["admin"] and (user_id != user["id"] or body.is_admin is not None or body.active is not None):
            raise HTTPException(403, "Solo un administrador puede cambiar los datos de otra persona.")
        if user_id == user["id"] and (body.is_admin is False or body.active is False):
            raise HTTPException(400, "No puedes quitarte a ti mismo la administración ni desactivar tu cuenta.")
        changes = body.model_dump(exclude_none=True)
        for field, value in changes.items():
            db.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (value.strip() if isinstance(value, str) else int(value), user_id))
        db.audit(user["org_id"], user, "usuario modificado", detail=f"{target['name']}: {', '.join(changes)}")
        return {"status": "ok"}

    @app.get("/invitations")
    def list_invitations(user: dict = Depends(admin)):
        return {"invitations": db.query("SELECT code, email, created FROM invitations WHERE org_id = ? AND used = 0 ORDER BY created DESC",
                                        (user["org_id"],))}

    @app.post("/invitations", status_code=201)
    def invite(body: InviteBody, user: dict = Depends(admin)):
        email = body.email.strip().lower()
        if not _EMAIL.match(email):
            raise HTTPException(400, "Correo no válido.")
        if db.one("SELECT id FROM users WHERE email = ?", (email,)):
            raise HTTPException(409, "Ya existe una cuenta con ese correo.")
        code = new_code()
        db.execute("INSERT INTO invitations (code, org_id, email, created_by, created) VALUES (?,?,?,?,?)",
                   (code, user["org_id"], email, user["id"], time.time()))
        db.audit(user["org_id"], user, "invitación enviada", detail=email)
        return {"code": code, "email": email}

    @app.delete("/invitations/{code}")
    def revoke_invitation(code: str, user: dict = Depends(admin)):
        db.execute("DELETE FROM invitations WHERE code = ? AND org_id = ? AND used = 0", (code, user["org_id"]))
        return {"status": "ok"}

    # --- projects and roles
    @app.get("/projects")
    def list_projects(user: dict = Depends(current)):
        rows = db.query("SELECT id, name, github_repo FROM projects WHERE org_id = ? ORDER BY name", (user["org_id"],))
        if not user["admin"]:
            rows = [r for r in rows if r["id"] in user["roles"]]
        for r in rows:
            r["role"] = "admin" if user["admin"] and r["id"] not in user["roles"] else user["roles"].get(r["id"])
            r["members"] = db.query(
                "SELECT u.id, u.name, u.github_login, m.role FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = ? AND m.project_id = ? ORDER BY u.name", (user["org_id"], r["id"]))
        return {"projects": rows}

    @app.post("/projects", status_code=201)
    def create_project(body: ProjectBody, user: dict = Depends(admin)):
        if not _PROJECT_ID.match(body.id):
            raise HTTPException(400, "Identificador no válido: letras, números, «.», «_» o «-», hasta 64.")
        if db.one("SELECT id FROM projects WHERE org_id = ? AND id = ?", (user["org_id"], body.id)):
            raise HTTPException(409, f"El proyecto '{body.id}' ya existe.")
        try:
            repo = normalize_repo(body.github_repo) if body.github_repo else None
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.execute("INSERT INTO projects (org_id, id, name, github_repo, created) VALUES (?,?,?,?,?)",
                   (user["org_id"], body.id, body.name.strip(), repo, time.time()))
        db.audit(user["org_id"], user, "proyecto creado", body.id, repo or "")
        return {"id": body.id, "name": body.name.strip(), "github_repo": repo}

    @app.patch("/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch, user: dict = Depends(current)):
        project_of(user["org_id"], project_id)
        require(user, R.LINK_REPO, project_id)
        if body.github_repo is not None:
            try:
                repo = normalize_repo(body.github_repo) if body.github_repo.strip() else None
            except ValueError as e:
                raise HTTPException(400, str(e))
            db.execute("UPDATE projects SET github_repo = ? WHERE org_id = ? AND id = ?", (repo, user["org_id"], project_id))
            db.audit(user["org_id"], user, "repositorio de GitHub vinculado", project_id, repo or "ninguno")
        if body.name:
            db.execute("UPDATE projects SET name = ? WHERE org_id = ? AND id = ?", (body.name.strip(), user["org_id"], project_id))
        return {"status": "ok"}

    @app.delete("/projects/{project_id}")
    def delete_project(project_id: str, user: dict = Depends(admin)):
        project_of(user["org_id"], project_id)
        db.execute("DELETE FROM memberships WHERE org_id = ? AND project_id = ?", (user["org_id"], project_id))
        db.execute("DELETE FROM projects WHERE org_id = ? AND id = ?", (user["org_id"], project_id))
        db.audit(user["org_id"], user, "proyecto eliminado", project_id)
        return {"status": "ok"}

    @app.put("/projects/{project_id}/members/{user_id}")
    def set_member(project_id: str, user_id: int, body: MemberBody, user: dict = Depends(current)):
        project_of(user["org_id"], project_id)
        require(user, R.MANAGE_MEMBERS, project_id)
        if body.role not in R.ROLES:
            raise HTTPException(400, f"Rol desconocido. Usa: {', '.join(R.ROLES)}.")
        target = db.one("SELECT * FROM users WHERE id = ? AND org_id = ? AND active = 1", (user_id, user["org_id"]))
        if not target:
            raise HTTPException(404, "Usuario no encontrado.")
        db.execute("INSERT INTO memberships (org_id, project_id, user_id, role) VALUES (?,?,?,?) "
                   "ON CONFLICT (org_id, project_id, user_id) DO UPDATE SET role = excluded.role",
                   (user["org_id"], project_id, user_id, body.role))
        db.audit(user["org_id"], user, "rol asignado", project_id, f"{target['name']}: {body.role}")
        return {"status": "ok"}

    @app.delete("/projects/{project_id}/members/{user_id}")
    def remove_member(project_id: str, user_id: int, user: dict = Depends(current)):
        project_of(user["org_id"], project_id)
        require(user, R.MANAGE_MEMBERS, project_id)
        target = db.one("SELECT name FROM users WHERE id = ? AND org_id = ?", (user_id, user["org_id"]))
        db.execute("DELETE FROM memberships WHERE org_id = ? AND project_id = ? AND user_id = ?", (user["org_id"], project_id, user_id))
        db.audit(user["org_id"], user, "rol quitado", project_id, target["name"] if target else str(user_id))
        return {"status": "ok"}

    # --- audit
    @app.post("/audit", status_code=201)
    def post_audit(body: AuditBody, user: dict = Depends(current)):
        """Events that Beacon Local reports (queries, syncs, deletions). Without the question text or any code."""
        db.audit(user["org_id"], user, body.action, body.project_id, body.detail, source="local")
        return {"status": "ok"}

    @app.get("/audit")
    def get_audit(project_id: Optional[str] = None, limit: int = 100, user: dict = Depends(current)):
        limit = max(1, min(limit, 500))
        if project_id:
            require(user, R.VIEW_AUDIT, project_id)
            rows = db.query("SELECT * FROM audit WHERE org_id = ? AND project_id = ? ORDER BY ts DESC LIMIT ?",
                            (user["org_id"], project_id, limit))
        elif user["admin"]:
            rows = db.query("SELECT * FROM audit WHERE org_id = ? ORDER BY ts DESC LIMIT ?", (user["org_id"], limit))
        else:
            visible = [p for p in user["roles"] if R.can(user, R.VIEW_AUDIT, p)]
            if not visible:
                raise HTTPException(403, "Tu rol no permite ver la auditoría.")
            marks = ",".join("?" * len(visible))
            rows = db.query(f"SELECT * FROM audit WHERE org_id = ? AND project_id IN ({marks}) ORDER BY ts DESC LIMIT ?",
                            (user["org_id"], *visible, limit))
        return {"events": rows}

    # --- GitHub (metadata of issues and commits)
    def github_for(user: dict, project_id: str):
        project = project_of(user["org_id"], project_id)
        require(user, R.ASK, project_id)
        if not project["github_repo"]:
            raise HTTPException(409, "El proyecto no tiene un repositorio de GitHub vinculado.")
        org = db.one("SELECT github_token FROM orgs WHERE id = ?", (user["org_id"],))
        return hub.github_factory(org["github_token"]), project["github_repo"]

    def github_call(fn):
        try:
            return fn()
        except GitHubError as e:
            raise HTTPException(e.status, str(e))

    @app.get("/projects/{project_id}/github/issues")
    def github_issues(project_id: str, mine: bool = False, user: dict = Depends(current)):
        gh, repo = github_for(user, project_id)
        if mine and not user["github_login"]:
            return {"repo": repo, "issues": [], "needs_github_login": True}
        issues = github_call(lambda: gh.issues(repo, assignee=user["github_login"] if mine else None))
        return {"repo": repo, "issues": issues}

    @app.get("/projects/{project_id}/github/issues/{number}")
    def github_issue(project_id: str, number: int, user: dict = Depends(current)):
        gh, repo = github_for(user, project_id)
        return github_call(lambda: gh.issue(repo, number))

    @app.get("/projects/{project_id}/github/activity")
    def github_activity(project_id: str, author: Optional[str] = None, user: dict = Depends(current)):
        gh, repo = github_for(user, project_id)
        return {"repo": repo, "commits": github_call(lambda: gh.commits(repo, author=author))}

    return app


def default_app() -> FastAPI:
    return create_app(Hub(Path(os.environ.get("BEACON_HUB_DATA", "hub-data"))))
