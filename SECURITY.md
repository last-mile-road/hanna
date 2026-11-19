# Security policy

HANNA runs a public test server and, at a later stage, will hold cards supplied
by country teams. Vulnerabilities in authentication, tenancy, file upload, job
submission or key handling are taken seriously.

## Reporting

Do not open a public issue for a vulnerability. Email the maintainer:

**TODO: security contact address (set before the repository is published)**

Include what you found, where, how to reproduce it, and what an attacker could
do with it. A proof of concept helps; a screenshot of a leaked secret is enough
to start.

You will get an acknowledgement within five working days. If the report is
valid, expect a fix or a mitigation plan within 90 days, and credit in the
release note unless you prefer otherwise. There is no bug bounty.

Please give us the 90 days before publishing. If a secret is exposed, we will
revoke it first and tell you when it is safe.

## Scope

In scope:

- the web portal and its API (authentication, sessions, invitations, roles);
- cross-country access — any way one country's account can read or change
  another country's data;
- upload handling — a file that escapes validation, executes, or fills the
  disk;
- job submission and GPU quota bypass;
- secrets committed to the repository or exposed by the running service.

Out of scope:

- extraction accuracy (that is a bug, not a vulnerability — open an issue);
- denial of service by volume on a test server with no service-level agreement;
- findings from automated scanners with no demonstrated impact;
- social engineering of the maintainer.

## What we already know

The test server holds dummy cards only, by rule. If you find a path that
accepts a real patient card, or that keeps an uploaded image beyond its purge
clock, report it: that is a data-protection defect even when the content is
dummy.
