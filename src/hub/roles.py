"""Roles per project (Scrum plus the rest of the company) and what each one may do. Shared by the Hub and Beacon Local."""

PRODUCT_OWNER = "product_owner"
SCRUM_MASTER = "scrum_master"
TECH_LEAD = "tech_lead"      # the senior who reviews and corrects what the AI drafts
DEVELOPER = "developer"
QA = "qa"
SUPPORT = "support"          # answers customers: plain-language explanations, no code
PRODUCT = "product"          # analysts and designers: what the system does, in business terms
SECURITY = "security"        # reviews access and who did what
OBSERVER = "observer"        # management or stakeholders: plain language, no individual metrics
ROLES = (PRODUCT_OWNER, SCRUM_MASTER, TECH_LEAD, DEVELOPER, QA, SUPPORT, PRODUCT, SECURITY, OBSERVER)
LABELS = {PRODUCT_OWNER: "Product Owner", SCRUM_MASTER: "Scrum Master", TECH_LEAD: "Líder técnico", DEVELOPER: "Desarrollador",
          QA: "QA", SUPPORT: "Soporte", PRODUCT: "Producto", SECURITY: "Seguridad", OBSERVER: "Observador"}

# Roles whose answers are explained without code.
PLAIN_LANGUAGE = frozenset({SUPPORT, PRODUCT, OBSERVER})

# Actions on a project. The organization administrator may do all of them, plus everything that is not tied to a
# project (people, projects, license, model server, disk space).
ASK = "ask"                    # query the code, read docs, see issues and team activity
SYNC = "sync"                  # sync the index and documentation, automatic watcher
VIEW_AUDIT = "view_audit"      # see who did what in the project
MANAGE_MEMBERS = "manage_members"
LINK_REPO = "link_repo"        # change the GitHub repository linked to the project
VERIFY = "verify"              # correct and verify generated documentation and answers

PERMISSIONS = {
    PRODUCT_OWNER: {ASK, SYNC, VIEW_AUDIT, MANAGE_MEMBERS, LINK_REPO},
    SCRUM_MASTER: {ASK, SYNC, VIEW_AUDIT},
    TECH_LEAD: {ASK, SYNC, VIEW_AUDIT, VERIFY},
    DEVELOPER: {ASK},
    QA: {ASK},
    SUPPORT: {ASK},
    PRODUCT: {ASK},
    SECURITY: {ASK, VIEW_AUDIT},
    OBSERVER: {ASK},
}


def can(claims: dict, action: str, project_id: str | None = None) -> bool:
    """Whether the person in `claims` (session claims: admin, roles) may do `action` on `project_id`."""
    if claims.get("admin"):
        return True
    if project_id is None:
        return False
    role = (claims.get("roles") or {}).get(project_id)
    return action in PERMISSIONS.get(role, set())
