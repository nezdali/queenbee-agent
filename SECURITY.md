# Security Policy

QueenBee Agent can generate and execute Python tools at runtime. That makes security reports especially important.

## Supported versions

QueenBee is currently pre-1.0. Security fixes are applied to the latest version on the `main` branch.

| Version | Supported |
| --- | --- |
| `main` / latest | Yes |
| older commits or forks | No |

## Important security model

Generated tools currently execute **in-process and are not sandboxed**.

QueenBee reduces risk through several layers:

- forbidden-intent checks before generation for non-admin users;
- forbidden-code pattern checks after generation;
- LLM-based security review for non-admin generated tools;
- administrator Approve / Reject flow;
- RBAC-aware tool visibility and execution;
- pending/rejected states that block execution;
- hard timeouts on tool invocation.

These controls reduce risk but do not make generated code safe to run in hostile or multi-tenant environments.

Administrators should assume that approved generated tools have the same effective access as the QueenBee process itself, including any files, network access, environment variables, and credentials available to that process.

## Deployment recommendations

For production deployments:

- run QueenBee as a dedicated, unprivileged OS user;
- do not run the process as root;
- expose only the secrets and credentials QueenBee actually needs;
- prefer scoped API keys and least-privilege identities;
- keep admin access restricted to fully trusted users;
- separate high-risk integrations from low-risk public tools where possible;
- consider container, VM, or process-level isolation before allowing untrusted users to generate tools;
- review generated tools before approval when they access files, networks, credentials, smart-home systems, or external APIs;
- rotate credentials immediately if you suspect a generated tool exposed or misused them.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a vulnerability that could expose secrets, enable unauthorized code execution, bypass RBAC, bypass tool approval, or affect deployed instances.

Prefer GitHub's private vulnerability reporting / Security Advisory flow for this repository when available.

If private reporting is not available, contact the repository maintainer privately through GitHub before publishing technical details.

When reporting, include:

- the affected commit or version;
- the relevant component or file;
- steps to reproduce;
- expected vs. actual behavior;
- realistic impact;
- whether exploitation requires admin privileges, a trusted user, or an untrusted user;
- a suggested fix, if you have one.

Please remove real API keys, Telegram tokens, credentials, cookies, private URLs, and personal data from reports.

## Security-sensitive areas

Reports involving these areas are particularly useful:

- `core/tool_factory.py`;
- generated-code validation;
- admin approval and review state;
- RBAC and permission enforcement;
- secret loading and environment handling;
- `importlib`-based generated tool execution;
- URL fetching and network access;
- prompt injection that changes tool behavior or reveals sensitive data;
- privilege escalation from a normal user to admin-only capabilities.

## Disclosure

Please allow reasonable time for a fix before publishing exploit details. Once a fix is available, coordinated disclosure is welcome.
