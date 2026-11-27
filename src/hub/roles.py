"""Scrum roles per project and what each one may do. Shared by the Hub and Beacon Local."""

PRODUCT_OWNER = "product_owner"
SCRUM_MASTER = "scrum_master"
TECH_LEAD = "tech_lead"      # the senior who reviews and corrects what the AI drafts
DEVELOPER = "developer"
ROLES = (PRODUCT_OWNER, SCRUM_MASTER, TECH_LEAD, DEVELOPER)

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
}


def can(claims: dict, action: str, project_id: str | None = None) -> bool:
    """Whether the person in `claims` (session claims: admin, roles) may do `action` on `project_id`."""
    if claims.get("admin"):
        return True
    if project_id is None:
        return False
    role = (claims.get("roles") or {}).get(project_id)
    return action in PERMISSIONS.get(role, set())
