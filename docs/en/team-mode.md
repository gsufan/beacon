**English** | [Español](../es/modo-equipo.md)

# Team mode: Beacon Hub and Beacon Local

Beacon has two parts:

| | Beacon Hub (cloud) | Beacon Local (machine or the organization's server) |
|---|---|---|
| What it does | Accounts, organization, Scrum roles per project, license and free trial, audit trail and GitHub connection | Indexes the code, answers questions with sources and generates the documentation with local models |
| What it stores | Metadata only: people, roles, project names, audit events and the organization's GitHub token | The code, the index and the documentation |

The code and the questions never travel to the Hub. Beacon Local verifies the session and the license with the Hub's
public key on every request, without calling it. Without a Hub configured, Beacon works on its own (standalone mode),
as before.

## Roles

Each person has a role in each project: the Scrum ones and those of the rest of the company, who use Beacon without changing their tools.

| Role | Can |
|---|---|
| Developer | Ask the code, read the documentation, see their issues and the team's activity, use "Where do I start?", the implementation plan, "What should we test?" and the impact radius |
| QA | Same as a developer; their main tool is "What should we test?" |
| Support, Product and Observer | Ask and read the documentation; answers come in plain language, without code, with the reference files at the end |
| Security | Ask, see the project's audit trail and the Overview |
| Tech lead | What a developer can, plus correct and verify documentation and answers, and see the Overview |
| Scrum Master | What a developer can, plus sync the project, see its audit trail and the Overview |
| Product Owner | The above, manage who takes part in the project and link its GitHub repository |
| Organization administrator | Everything: people, projects, plan, model server, disk space, and deleting projects |

## License and trial

Creating the organization starts a 30-day trial with every feature. After that, a plan is contracted per user per
month. The license is signed by the Hub; if it expires, Beacon Local blocks queries and documentation until it is
renewed (the Team page stays available to the administrator). In the prototype the payment is simulated and recorded
in the audit trail.

## "My work" and "Where do I start?"

With the project's GitHub repository linked, the My work page shows the issues assigned to the person (or all open
ones) and the team's latest commits, with their author and the issues they mention. The "Where do I start?" button
takes an issue's title and description and asks the code which files and functions to review first; the answer
cites its sources like any other.

The "Daily stand-up draft" button writes the person's stand-up in three sections (yesterday, today and blockers) from
their commits since the last working day (Friday on Mondays), local and on GitHub, and their open issues; it adds no
work that does not appear in that data and can be copied or shared on Slack.

To see "my issues", each person enters their GitHub username (in My work or in Team > People). Without a GitHub token
for the organization, the Hub only reads public repositories, within GitHub's rate limit.

## Team integrations

They are set up in Team > Integrations (administrator only); the credentials stay in the Hub and never reach the
browsers.

| Tool | What it does in Beacon | What it needs |
|---|---|---|
| GitHub | The project's backlog (open issues, assigned to each person) and commit activity; commenting on an issue | A repository linked to the project; the organization's token (read, plus write on issues to comment) |
| Jira Cloud | The project's backlog from Jira (if the project has a Jira key, it is used instead of GitHub); commenting on an issue | Site `https://<site>.atlassian.net`, email and API token; the project key (for example, `BEAC`); each person enters their Jira email or accountId |
| Slack | Team notices (someone joined, a role changed, a project was added or synced, the plan was contracted) and "Share in Slack" for an answer | An incoming webhook (`https://hooks.slack.com/services/...`), which picks the channel |

From a "Where do I start?" answer you can **comment on the issue** (GitHub or Jira) or **share it in Slack**; both
ask for confirmation, because they send the answer and its reference files (not the code) to that tool, and both are
recorded in the audit trail. Beacon sends nothing to Slack on its own except the team notices, which carry no
questions or code.

**Who knows these files.** Next to each answer, Beacon Local shows who changed the cited files the most, read from the
repository's git history (names and number of commits, no emails). It needs a clone with history; a copy without
history only shows whoever imported it.

**See someone else's work.** In My work, "See the work of…" shows the issues assigned to a project member and their
recent commits: useful to pick up their work, for example, if they went on vacation.

## Getting started

### With Docker Compose (Hub and Beacon on the same machine)

```bash
docker compose -f docker-compose.yml -f docker-compose.equipo.yml up -d --build
```

Open http://localhost:8000, choose "Create organization" and continue in Team > Projects to add the projects and
their repositories.

### Without Docker

```bash
beacon-hub --port 8100 --data hub-data      # the Hub
```

In each Beacon Local's `config/config.yaml`:

```yaml
hub:
  url: http://hub-server:8100
```

The first time Beacon connects, it saves the Hub's public key (`public_key`) in that same section; from then on it does
not accept a different one unless it is changed by hand. It can also be set with the `BEACON_HUB_URL` and
`BEACON_HUB_PUBLIC_KEY` variables.

## Audit trail

The Hub records who did what and when: sign-ins, invitations, roles, projects, plan, and from Beacon Local the
queries, syncs, deletions and model server changes. Neither the question text nor the code is stored. The
administrator sees the whole organization; the Product Owner and the Scrum Master, their projects.
