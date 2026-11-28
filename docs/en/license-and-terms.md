**English** | [Español](../es/licencia-y-terminos.md)

# License, delivery and terms of use

Beacon's code is public and its academic, educational, research or personal use is free; commercial use requires a
subscription or written permission from the authors (see [LICENSE](../../LICENSE)). This document summarizes how the product is protected
and what an organization accepts when using it. It is an academic exercise: the company that sells it (Almenara
Software SpA) is fictitious.

## How the product is protected (four layers)

| Layer | What it does |
|---|---|
| 1. The team's value lives in Beacon Hub | Accounts, roles, the GitHub, Jira and Slack integrations, the audit trail, installations and what the team builds together depend on the Hub. A copy of Beacon Local without the Hub has no team, integrations or updates |
| 2. Leased, revocable license | Each Beacon Local registers with the Hub and renews its license with a heartbeat every two minutes; without contact, the license lasts three days. The Hub can revoke an installation and, on its next contact, Beacon Local wipes everything it keeps |
| 3. Compiled delivery | Customers receive a container image with Beacon's code compiled to native modules (Nuitka), without the source files, in the "commercial" edition, which does not serve without registration and a valid license |
| 4. Contract | The terms below |

No protection of software installed at the customer is absolute; the goal is that copying Beacon is not worth it and
that a leak can be traced.

## Terms of use (summary)

1. **Commercial use.** The organization may use Beacon while it keeps a valid subscription, within the plan, number of
   users and period its license states. The free trial lasts 30 days.
2. **Prohibitions.** Without written permission, selling or redistributing Beacon commercially, offering it as a
   service, or removing or bypassing the license check in a copy others use is not allowed. Noncommercial use
   (studying, modifying and sharing the code for those purposes) is free.
3. **Audit.** Almenara may verify, with prior notice, that the use matches the contracted license.
4. **Data.** The source code, the index, the documentation and the questions stay in the organization's
   infrastructure; Almenara does not access them. Beacon Hub processes users' personal data (name, email, GitHub and
   Jira users, roles and audit trail) as a processor on behalf of the organization, only to provide the service, under
   Chile's Law 21.719.
5. **Termination and offboarding.** If the subscription ends, the license stops renewing and Beacon Local stops
   serving. If the organization revokes an installation or removes a person, the affected installations wipe the data
   they keep on their next contact with the Hub. People's own folders (a local repository) are not deleted.
6. **Earlier versions.** Versions published before November 27, 2026 under the MIT License remain under that license.
