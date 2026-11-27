"""Beacon Hub: the cloud side of Beacon. Organizations, people and Scrum roles, licenses with a free trial, the
audit trail and the team's tools (GitHub or Jira for the backlog, Slack for notices). It stores metadata only; source
code stays in Beacon Local.
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
from hub.jira import JiraClient, JiraError, normalize_project, normalize_site
from hub.slack import SlackClient, SlackError, normalize_webhook
from hub.security import (
    InvalidTokenError, hash_password, load_or_create_key, new_code, public_key_text, sign_token, verify_password, verify_token,
)

# Short sessions: a person who is deactivated loses access in minutes, not hours (the interface renews them).
SESSION_TTL = 15 * 60
# Licenses are leased: a browser license lasts a week at most, an installation's lease three days without contact.
LICENSE_LEASE = 7 * 86400
INSTANCE_LEASE = 3 * 86400
REFRESH_GRACE = 12 * 3600
TRIAL_DAYS = 30
PLANS = {
    "trial": {"label": "Prueba", "seats": 5, "price_per_user": 0},
    "equipo": {"label": "Equipo", "seats": 15, "price_per_user": 14990},
    "empresa": {"label": "Empresa", "seats": 60, "price_per_user": 11990},
}
_PROJECT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ROLE_LABEL = {R.PRODUCT_OWNER: "Product Owner", R.SCRUM_MASTER: "Scrum Master", R.TECH_LEAD: "Líder técnico", R.DEVELOPER: "Desarrollador"}
# Actions reported by Beacon Local that the team hears about in Slack (queries are not announced).
_NOTIFY_LOCAL = {"sincronización": ":arrows_counterclockwise:", "proyecto quitado de Beacon Local": ":wastebasket:",
                 "proyecto registrado en Beacon Local": ":new:"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Hub:
    """State of one Hub instance: database, signing key and the GitHub client factory (replaceable in tests)."""

    def __init__(self, data_dir: Path, github_factory=None, jira_factory=None, slack_factory=None):
        self.db = Database(data_dir / "hub.sqlite3")
        self.key = load_or_create_key(data_dir / "hub_signing_key.pem")
        self.public_key = public_key_text(self.key)
        self.github_factory = github_factory or (lambda token: GitHubClient(token))
        self.jira_factory = jira_factory or (lambda site, email, token: JiraClient(site, email, token))
        self.slack_factory = slack_factory or (lambda webhook: SlackClient(webhook))

    # --- tokens
    def roles_of(self, user: dict) -> dict:
        rows = self.db.query("SELECT project_id, role FROM memberships WHERE user_id = ?", (user["id"],))
        return {r["project_id"]: r["role"] for r in rows}

    def session_claims(self, user: dict) -> dict:
        return {"typ": "session", "sub": user["id"], "org": user["org_id"], "name": user["name"], "email": user["email"],
                "admin": bool(user["is_admin"]), "github": user["github_login"], "roles": self.roles_of(user),
                "exp": int(time.time()) + SESSION_TTL}

    def license_token(self, org: dict, lease: int = LICENSE_LEASE) -> str:
        """The organization's license, leased: it expires at the end of the paid period or after `lease`, whichever is
        first, so a customer who stops paying (or whose license is revoked) stops working within days."""
        now = int(time.time())
        return sign_token(self.key, {"typ": "license", "org": org["id"], "org_name": org["name"], "plan": org["plan"],
                                     "seats": org["seats"], "iat": now, "exp": int(min(org["period_end"], now + lease))})

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
    jira_user: Optional[str] = None
    is_admin: Optional[bool] = None
    active: Optional[bool] = None


class ProjectBody(BaseModel):
    id: str
    name: str = Field(..., min_length=1, max_length=80)
    github_repo: Optional[str] = None
    jira_project: Optional[str] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    github_repo: Optional[str] = None
    jira_project: Optional[str] = None


class JiraBody(BaseModel):
    site: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=200)
    token: str = Field(default="", max_length=300)


class SlackBody(BaseModel):
    webhook: str = Field(default="", max_length=300)


class CommentBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)


class SlackMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=3500)


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


class VerificationBody(BaseModel):
    kind: str = Field(..., pattern="^(doc|answer)$")
    ref: str = Field(..., min_length=1, max_length=500)
    digest: str = Field(..., pattern="^[0-9a-f]{64}$")


class InstanceRegister(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    personal: bool = False


class Heartbeat(BaseModel):
    instance_id: str
    secret: str
    version: str = Field(default="", max_length=40)


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
                "expired": org["period_end"] < time.time(), "github_connected": bool(org["github_token"]),
                "jira_connected": bool(org["jira_token"]), "jira_site": org["jira_site"], "slack_connected": bool(org["slack_webhook"])}

    def notify(org_id: int, text: str) -> None:
        """Team notice in the organization's Slack channel, if it set one up. Never blocks the action."""
        org = db.one("SELECT slack_webhook FROM orgs WHERE id = ?", (org_id,))
        if org and org["slack_webhook"]:
            hub.slack_factory(org["slack_webhook"]).send_later(text)

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
    def refresh(authorization: str = Header(default="")):
        """New session and license with the current roles and plan. Accepts a session that expired up to 12 hours ago,
        so a short session does not force a new login after a pause, but only while the person is still active."""
        try:
            claims = verify_token(hub.public_key, authorization.removeprefix("Bearer ").strip(), "session", leeway=REFRESH_GRACE)
        except InvalidTokenError as e:
            raise HTTPException(401, f"Sesión inválida: {e}")
        user = db.one("SELECT * FROM users WHERE id = ? AND active = 1", (claims["sub"],))
        if not user:
            raise HTTPException(401, "La cuenta no existe o fue desactivada.")
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
        notify(inv["org_id"], f":wave: {user['name']} se unió al equipo en Beacon.")
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
        notify(org["id"], f":white_check_mark: Plan {PLANS[body.plan]['label']} activo en Beacon por 30 días.")
        return hub.session_response(db.one("SELECT * FROM users WHERE id = ?", (user["id"],)))

    @app.put("/org/github")
    def set_github(body: GitHubBody, user: dict = Depends(admin)):
        db.execute("UPDATE orgs SET github_token = ? WHERE id = ?", (body.token.strip() or None, user["org_id"]))
        db.audit(user["org_id"], user, "token de GitHub actualizado" if body.token.strip() else "token de GitHub quitado")
        return {"github_connected": bool(body.token.strip())}

    @app.put("/org/jira")
    def set_jira(body: JiraBody, user: dict = Depends(admin)):
        if not body.token.strip():
            db.execute("UPDATE orgs SET jira_site = NULL, jira_email = NULL, jira_token = NULL WHERE id = ?", (user["org_id"],))
            db.audit(user["org_id"], user, "Jira desconectado")
            return {"jira_connected": False}
        try:
            site = normalize_site(body.site)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not _EMAIL.match(body.email.strip()):
            raise HTTPException(400, "Correo de Jira no válido.")
        db.execute("UPDATE orgs SET jira_site = ?, jira_email = ?, jira_token = ? WHERE id = ?",
                   (site, body.email.strip(), body.token.strip(), user["org_id"]))
        db.audit(user["org_id"], user, "Jira conectado", detail=site)
        return {"jira_connected": True}

    @app.put("/org/slack")
    def set_slack(body: SlackBody, user: dict = Depends(admin)):
        try:
            webhook = normalize_webhook(body.webhook) if body.webhook.strip() else None
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.execute("UPDATE orgs SET slack_webhook = ? WHERE id = ?", (webhook, user["org_id"]))
        db.audit(user["org_id"], user, "Slack conectado" if webhook else "Slack desconectado")
        return {"slack_connected": bool(webhook)}

    @app.post("/org/slack/test")
    def test_slack(user: dict = Depends(admin)):
        org = db.one("SELECT slack_webhook, name FROM orgs WHERE id = ?", (user["org_id"],))
        if not org["slack_webhook"]:
            raise HTTPException(409, "La organización no tiene Slack conectado.")
        try:
            hub.slack_factory(org["slack_webhook"]).send(f":satellite_antenna: Beacon quedó conectado a este canal ({org['name']}).")
        except SlackError as e:
            raise HTTPException(502, str(e))
        return {"status": "ok"}

    # --- Beacon Local installations: registration, heartbeat with the lease, and remote revocation (wipe)
    @app.post("/instances", status_code=201)
    def register_instance(body: InstanceRegister, user: dict = Depends(current)):
        """Registers a Beacon Local. A shared server is registered by an administrator; a personal installation
        belongs to the person who registers it and is revoked when that person is deactivated."""
        if not body.personal and not user["admin"]:
            raise HTTPException(403, "Solo un administrador registra una instalación compartida.")
        instance_id, secret = new_code(12), new_code(24)
        db.execute("INSERT INTO instances (id, org_id, user_id, name, secret_hash, created, last_seen) VALUES (?,?,?,?,?,?,?)",
                   (instance_id, user["org_id"], user["id"] if body.personal else None, body.name.strip(),
                    hash_password(secret), time.time(), time.time()))
        db.audit(user["org_id"], user, "instalación registrada", detail=f"{body.name.strip()} ({'personal' if body.personal else 'compartida'})")
        return {"instance_id": instance_id, "secret": secret, "public_key": hub.public_key}

    @app.post("/instances/heartbeat")
    def instance_heartbeat(body: Heartbeat):
        """Called by Beacon Local every few minutes. Returns a signed status: the leased license, or the order to wipe."""
        inst = db.one("SELECT * FROM instances WHERE id = ?", (body.instance_id,))
        if not inst or not verify_password(body.secret, inst["secret_hash"]):
            raise HTTPException(401, "Instalación desconocida.")
        org = db.one("SELECT * FROM orgs WHERE id = ?", (inst["org_id"],))
        owner = db.one("SELECT active FROM users WHERE id = ?", (inst["user_id"],)) if inst["user_id"] else None
        revoked = bool(inst["revoked"]) or (owner is not None and not owner["active"])
        db.execute("UPDATE instances SET last_seen = ?, version = ? WHERE id = ?", (time.time(), body.version, inst["id"]))
        status = {"typ": "instance", "instance": inst["id"], "org": org["id"], "revoked": revoked,
                  "iat": int(time.time()), "exp": int(time.time()) + INSTANCE_LEASE}
        if revoked and not inst["wiped"]:
            db.execute("UPDATE instances SET wiped = 1 WHERE id = ?", (inst["id"],))
            db.audit(org["id"], None, "instalación borrada a distancia", detail=inst["name"])
        return {"status": sign_token(hub.key, status), "license": None if revoked else hub.license_token(org, INSTANCE_LEASE)}

    @app.get("/instances")
    def list_instances(user: dict = Depends(admin)):
        rows = db.query("SELECT i.id, i.name, i.user_id, u.name AS user_name, i.created, i.last_seen, i.revoked, i.wiped, i.version "
                        "FROM instances i LEFT JOIN users u ON u.id = i.user_id WHERE i.org_id = ? ORDER BY i.created",
                        (user["org_id"],))
        return {"instances": rows}

    @app.post("/instances/{instance_id}/revoke")
    def revoke_instance(instance_id: str, user: dict = Depends(admin)):
        inst = db.one("SELECT name FROM instances WHERE id = ? AND org_id = ?", (instance_id, user["org_id"]))
        if not inst:
            raise HTTPException(404, "Instalación no encontrada.")
        db.execute("UPDATE instances SET revoked = 1 WHERE id = ?", (instance_id,))
        db.audit(user["org_id"], user, "instalación revocada", detail=inst["name"])
        notify(user["org_id"], f":no_entry: La instalación {inst['name']} fue revocada; sus datos se borrarán en su próximo contacto.")
        return {"status": "ok"}

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
        if body.active is False:
            # Offboarding: no roles left, and the installations on that person's own machine are wiped on their next contact.
            db.execute("DELETE FROM memberships WHERE org_id = ? AND user_id = ?", (user["org_id"], user_id))
            n = db.execute("UPDATE instances SET revoked = 1 WHERE org_id = ? AND user_id = ? AND revoked = 0", (user["org_id"], user_id))
            db.audit(user["org_id"], user, "baja de persona", detail=f"{target['name']}: roles quitados e instalaciones personales revocadas")
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
        rows = db.query("SELECT id, name, github_repo, jira_project FROM projects WHERE org_id = ? ORDER BY name", (user["org_id"],))
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
            jira_key = normalize_project(body.jira_project) if body.jira_project else None
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.execute("INSERT INTO projects (org_id, id, name, github_repo, jira_project, created) VALUES (?,?,?,?,?,?)",
                   (user["org_id"], body.id, body.name.strip(), repo, jira_key, time.time()))
        db.audit(user["org_id"], user, "proyecto creado", body.id, repo or jira_key or "")
        notify(user["org_id"], f":new: Proyecto {body.name.strip()} agregado al equipo en Beacon.")
        return {"id": body.id, "name": body.name.strip(), "github_repo": repo, "jira_project": jira_key}

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
        if body.jira_project is not None:
            try:
                key = normalize_project(body.jira_project) if body.jira_project.strip() else None
            except ValueError as e:
                raise HTTPException(400, str(e))
            db.execute("UPDATE projects SET jira_project = ? WHERE org_id = ? AND id = ?", (key, user["org_id"], project_id))
            db.audit(user["org_id"], user, "proyecto de Jira vinculado", project_id, key or "ninguno")
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
        notify(user["org_id"], f":busts_in_silhouette: {target['name']} tiene ahora el rol {_ROLE_LABEL[body.role]} en {project_id}.")
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
        if body.action in _NOTIFY_LOCAL:
            notify(user["org_id"], f"{_NOTIFY_LOCAL[body.action]} {user['name']}: {body.action} en {body.project_id or 'Beacon'}.")
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

    def tracker_for(user: dict, project_id: str):
        """The project's backlog: Jira if it has a Jira project and the organization connected Jira, otherwise GitHub."""
        project = project_of(user["org_id"], project_id)
        require(user, R.ASK, project_id)
        org = db.one("SELECT * FROM orgs WHERE id = ?", (user["org_id"],))
        if project["jira_project"] and org["jira_token"]:
            return "jira", hub.jira_factory(org["jira_site"], org["jira_email"], org["jira_token"]), project["jira_project"]
        if project["github_repo"]:
            return "github", hub.github_factory(org["github_token"]), project["github_repo"]
        raise HTTPException(409, "El proyecto no tiene un backlog vinculado (repositorio de GitHub o proyecto de Jira).")

    def tracker_call(fn):
        try:
            return fn()
        except (GitHubError, JiraError) as e:
            raise HTTPException(e.status, str(e))

    def login_of(person: dict, source: str) -> Optional[str]:
        return person["jira_user"] if source == "jira" else person["github_login"]

    @app.get("/projects/{project_id}/issues")
    def project_issues(project_id: str, mine: bool = False, assignee_id: Optional[int] = None, state: str = "open",
                       user: dict = Depends(current)):
        """Open issues of the project's backlog: all, the person's own (mine) or another member's (assignee_id)."""
        source, client, where = tracker_for(user, project_id)
        person = user
        if assignee_id is not None:
            person = db.one("SELECT * FROM users WHERE id = ? AND org_id = ?", (assignee_id, user["org_id"]))
            if not person:
                raise HTTPException(404, "Usuario no encontrado.")
        login = login_of(person, source) if (mine or assignee_id is not None) else None
        if (mine or assignee_id is not None) and not login:
            return {"source": source, "where": where, "issues": [], "needs_login": True, "person": person["name"]}
        closed = state == "closed"
        if source == "github":
            issues = tracker_call(lambda: client.issues(where, assignee=login, state="closed" if closed else "open"))
        else:
            issues = tracker_call(lambda: client.issues(where, assignee=login, done=closed))
        return {"source": source, "where": where, "issues": issues, "person": person["name"] if assignee_id is not None else None}

    @app.get("/projects/{project_id}/issues/{ref}")
    def project_issue(project_id: str, ref: str, user: dict = Depends(current)):
        source, client, where = tracker_for(user, project_id)
        if source == "github":
            if not ref.lstrip("#").isdigit():
                raise HTTPException(400, "Número de issue de GitHub no válido.")
            return tracker_call(lambda: client.issue(where, int(ref.lstrip("#"))))
        return tracker_call(lambda: client.issue(ref))

    @app.post("/projects/{project_id}/issues/{ref}/comment")
    def comment_issue(project_id: str, ref: str, body: CommentBody, user: dict = Depends(current)):
        """Writes in the backlog: a comment on the issue (for example, Beacon's answer to «where do I start?»)."""
        source, client, where = tracker_for(user, project_id)
        text = body.body.strip() + f"\n\n— Publicado desde Beacon por {user['name']}"
        if source == "github":
            if not ref.lstrip("#").isdigit():
                raise HTTPException(400, "Número de issue de GitHub no válido.")
            url = tracker_call(lambda: client.comment(where, int(ref.lstrip("#")), text))
        else:
            url = tracker_call(lambda: client.comment(ref, text))
        db.audit(user["org_id"], user, "comentario en la issue", project_id, ref)
        return {"url": url}

    @app.post("/projects/{project_id}/slack")
    def share_in_slack(project_id: str, body: SlackMessage, user: dict = Depends(current)):
        """Shares a message in the organization's Slack channel, on the person's explicit request."""
        project_of(user["org_id"], project_id)
        require(user, R.ASK, project_id)
        org = db.one("SELECT slack_webhook FROM orgs WHERE id = ?", (user["org_id"],))
        if not org["slack_webhook"]:
            raise HTTPException(409, "La organización no tiene Slack conectado.")
        try:
            hub.slack_factory(org["slack_webhook"]).send(f"*{user['name']}* compartió desde Beacon ({project_id}):\n{body.text}")
        except SlackError as e:
            raise HTTPException(502, str(e))
        db.audit(user["org_id"], user, "compartido en Slack", project_id)
        return {"status": "ok"}

    @app.post("/projects/{project_id}/verifications")
    def sign_verification(project_id: str, body: VerificationBody, user: dict = Depends(current)):
        """Signs that a tech lead verified a document or an answer. The Hub receives only a digest of the content,
        never the content; Beacon Local keeps the text and checks it against this signature."""
        project_of(user["org_id"], project_id)
        require(user, R.VERIFY, project_id)
        ts = time.time()
        db.execute("INSERT INTO verifications (org_id, project_id, kind, ref, digest, user_id, ts) VALUES (?,?,?,?,?,?,?)",
                   (user["org_id"], project_id, body.kind, body.ref, body.digest, user["id"], ts))
        db.audit(user["org_id"], user, "documento verificado" if body.kind == "doc" else "respuesta verificada", project_id, body.ref[:200])
        token = sign_token(hub.key, {"typ": "verification", "org": user["org_id"], "project": project_id, "kind": body.kind,
                                     "ref": body.ref, "digest": body.digest, "reviewer": user["name"], "iat": int(ts),
                                     "exp": int(ts) + 3650 * 86400})
        return {"token": token, "reviewer": user["name"]}

    @app.get("/projects/{project_id}/verifications/summary")
    def verification_summary(project_id: str, user: dict = Depends(current)):
        project_of(user["org_id"], project_id)
        require(user, R.ASK, project_id)
        rows = db.query("SELECT kind, COUNT(DISTINCT ref) AS n FROM verifications WHERE org_id = ? AND project_id = ? GROUP BY kind",
                        (user["org_id"], project_id))
        return {r["kind"]: r["n"] for r in rows}

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
    def github_activity(project_id: str, author: Optional[str] = None, assignee_id: Optional[int] = None, user: dict = Depends(current)):
        gh, repo = github_for(user, project_id)
        if assignee_id is not None:
            person = db.one("SELECT github_login FROM users WHERE id = ? AND org_id = ?", (assignee_id, user["org_id"]))
            if not person:
                raise HTTPException(404, "Usuario no encontrado.")
            if not person["github_login"]:
                return {"repo": repo, "commits": [], "needs_login": True}
            author = person["github_login"]
        return {"repo": repo, "commits": github_call(lambda: gh.commits(repo, author=author))}

    return app


def default_app() -> FastAPI:
    return create_app(Hub(Path(os.environ.get("BEACON_HUB_DATA", "hub-data"))))
