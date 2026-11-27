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

Each person has a Scrum role in each project:

| Role | Can |
|---|---|
| Developer | Ask the code, read the documentation, see their issues and the team's activity, use "Where do I start?" |
| Scrum Master | The above, sync the project and see its audit trail |
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

To see "my issues", each person enters their GitHub username (in My work or in Team > People). Without a GitHub token
for the organization, the Hub only reads public repositories, within GitHub's rate limit.

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
