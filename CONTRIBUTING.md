# Contributing to QueenBee Agent

Thanks for your interest in QueenBee Agent.

QueenBee is an experimental self-extending LLM agent, so contributions that improve safety, reliability, tool quality, provider compatibility, and developer experience are especially useful.

## Good ways to contribute

- Fix bugs or improve error handling.
- Add or improve tools under `tools/`.
- Improve the runtime tool-generation and review flow.
- Improve RBAC, security checks, or sandboxing.
- Add support for OpenAI-compatible providers.
- Improve documentation and examples.
- Add tests for existing behavior.

## Before opening a pull request

1. Fork the repository and create a branch from `main`.
2. Keep changes focused. Avoid unrelated refactors in the same PR.
3. Do not commit secrets, tokens, credentials, cookies, private URLs, or personal data.
4. If you add a new environment variable, document it in the README and `.env.example`.
5. If you add a new tool, keep it self-contained and document any required permissions or credentials.

## Local setup

```bash
git clone https://github.com/nezdali/queenbee-agent.git
cd queenbee-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Fill in the required values in `.env`, then run:

```bash
python bot.py
```

## Adding a tool

Tools are small Python modules that expose:

```python
async def run(context: dict) -> str:
    ...
```

Prefer tools that:

- have one clear purpose;
- return concise, user-facing output;
- handle API/network failures gracefully;
- avoid unnecessary dependencies;
- declare and respect the appropriate RBAC permission;
- never expose secrets in output or logs.

## Security-sensitive changes

Please call out security implications explicitly in the PR description when modifying:

- `core/tool_factory.py`;
- generated-code validation;
- RBAC or permission checks;
- secret handling;
- tool execution;
- admin approval behavior;
- URL fetching or external command execution.

Generated tools currently execute in-process and are not sandboxed, so changes that reduce that risk are particularly welcome.

## Pull requests

A good PR description should include:

- what changed;
- why it changed;
- how it was tested;
- any security or compatibility implications.

Small, reviewable pull requests are preferred.

## Reporting security issues

Please do not publish secrets, exploit details, or sensitive deployment information in a public issue.

For non-sensitive bugs and feature requests, use the GitHub issue templates.
